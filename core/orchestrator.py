"""
Novel OS - Workflow Orchestrator

Central orchestration system that coordinates agents through the novel writing workflow.
"""

import json
import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
from typing import Optional, Dict, Any, List, Literal
from datetime import datetime

# Import state manager
from state_manager import StoryState, Character, PlotThread, ChapterState, TimelineEvent, StyleProfile, initialize_project
from llm_client import LLMClient, LLMError
from model_router import ModelRouter
from state_parser import ingest_agent_output, normalize_agent_output, parse_agent_output
from continuity_engine import run_all as run_continuity_checks, summarize as summarize_findings, to_context_block
from prose_sanitize import sanitize_manuscript, apply_header_to_chapter, strip_em_dashes
from context_pack import build_context_pack, format_context_pack, slice_chapter_for_llm
from canon import build_canon_proposal
from proposals import ProposalStore


class NovelOrchestrator:
    """
    Orchestrates the novel writing workflow across all agents.
    """
    
    def __init__(
        self,
        project_path: str = ".",
        state_update_mode: Literal["legacy_apply", "proposal_only"] = "legacy_apply",
    ):
        self._validate_state_update_mode(state_update_mode)
        self.project_path = Path(project_path)
        self.state = StoryState(project_path)
        self.agents_dir = Path(__file__).parent.parent / "agents"
        self.templates_dir = Path(__file__).parent.parent / "templates"
        self.outputs_dir = self.project_path / "outputs"
        self.manuscript_dir = self.outputs_dir / "manuscript"
        self.feedback_dir = self.outputs_dir / "feedback"
        
        self._ensure_directories()
        self._llm: Optional[LLMClient] = None
        self._llms: Dict[str, LLMClient] = {}
        self._model_router = ModelRouter()
        # Book-level pipelines opt into exceptions so they can retry and record
        # a failed stage. Interactive legacy commands retain their friendly
        # print-and-return behavior.
        self.raise_llm_errors = False
        self.state_update_mode = state_update_mode
        self.last_canon_proposal_ids: List[str] = []

    @staticmethod
    def _validate_state_update_mode(mode: str) -> None:
        if mode not in ("legacy_apply", "proposal_only"):
            raise ValueError(
                "state_update_mode must be 'legacy_apply' or 'proposal_only'"
            )

    def _persist_canon_proposal(
        self,
        *,
        chapter_number: Optional[int],
        agent_name: str,
        normalized_response: str,
        source_path: Path,
    ) -> None:
        """Persist a parsed delta bound to the exact saved artifact bytes."""
        if chapter_number is None or not parse_agent_output(agent_name, normalized_response):
            return
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        proposal = build_canon_proposal(
            self.state,
            chapter_number,
            agent_name,
            source_sha,
            normalized_response,
        )
        persisted = ProposalStore(self.project_path).save(proposal)
        self.last_canon_proposal_ids.append(persisted.proposal_id)

    def _get_llm(self, agent_name: str = "writer") -> LLMClient:
        """Return a legacy override or a role-keyed cached client."""
        if self._llm is not None:
            return self._llm
        role = self._model_router.normalize_role(agent_name)
        if role not in self._llms:
            self._llms[role] = self._model_router.client_for(role)
        return self._llms[role]

    @property
    def llm(self) -> LLMClient:
        """Compatibility access to the default writer client."""
        return self._get_llm("writer")

    @llm.setter
    def llm(self, client: LLMClient) -> None:
        self._llm = client
        self._llms.clear()

    def runtime_provenance_for(self, agent_name: str) -> tuple[str, str]:
        """Return the resolved provider/model for an agent without credentials."""
        llm = self._get_llm(agent_name)
        return str(llm.provider), str(llm.model)

    def _run_agent_or_save_prompt(
        self,
        agent_name: str,
        user_prompt: str,
        prompt_path: Path,
        output_path: Path,
        dry_run: bool,
        label: str,
        chapter_number: Optional[int] = None,
        state_update_mode: Optional[Literal["legacy_apply", "proposal_only"]] = None,
    ) -> Optional[str]:
        """Either save the prompt (dry-run) or call the agent and save its output.

        On a successful real call, also ingest any state-update blocks the agent
        emitted into StoryState so persistent memory actually updates.
        """
        self.last_canon_proposal_ids = []
        resolved_mode = self.state_update_mode if state_update_mode is None else state_update_mode
        self._validate_state_update_mode(resolved_mode)
        prompt_path.write_text(user_prompt, encoding='utf-8')
        if dry_run:
            print(f"   [dry-run] Prompt saved: {prompt_path}")
            return None
        try:
            llm = self._get_llm(agent_name)
            print(f"   {label} ({llm.provider}:{llm.model})...")
            result = llm.run_agent(agent_name, user_prompt)
        except LLMError as e:
            print(f"❌ LLM call failed: {e}")
            print(f"   Prompt saved at {prompt_path} you can run it manually.")
            if self.raise_llm_errors:
                raise
            return None

        # Preserve the provider response before parsing/sanitizing it. A failed
        # output contract can then be diagnosed without another paid call.
        raw_path = output_path.with_suffix(output_path.suffix + ".raw")
        raw_path.write_text(result, encoding="utf-8")

        normalized_result = normalize_agent_output(result)
        manuscript_result = normalized_result
        if agent_name == "scribe" and self.raise_llm_errors and not re.search(
            r"\[SCRIBE_STATE_UPDATE\]", normalized_result, re.IGNORECASE
        ):
            raise LLMError("Scribe response is missing [SCRIBE_STATE_UPDATE]")
        if agent_name == "editor":
            match = re.search(
                r"\[REVISED(?:_|\s+)CHAPTER\](.*?)\[/REVISED(?:_|\s+)CHAPTER\]",
                normalized_result,
                re.IGNORECASE | re.DOTALL,
            )
            if match:
                manuscript_result = match.group(1).strip()
            elif self.raise_llm_errors:
                raise LLMError("Editor response is missing [REVISED_CHAPTER]")
            else:
                print("⚠️  Editor response is missing [REVISED_CHAPTER]; saving sanitized response.")

        # Only contract-valid responses may mutate persistent story state.
        if chapter_number is not None and resolved_mode == "legacy_apply":
            changes = ingest_agent_output(
                self.state, chapter_number, agent_name, normalized_result
            )
            if changes:
                print(f"   📝 State updates ({len(changes)}):")
                for line in changes:
                    print(f"      • {line}")

        # Manuscript stages: strip em dashes, HTML CHAPTER headers, and
        # agent bookkeeping blocks so humans never read machine chrome.
        saved = normalized_result
        if agent_name in ("scribe", "editor") and output_path.suffix == ".md":
            clean, meta = sanitize_manuscript(manuscript_result)
            saved = clean
            if chapter_number is not None and resolved_mode == "legacy_apply":
                chapter = self.state.get_chapter(chapter_number)
                apply_header_to_chapter(chapter, meta)
                if chapter and clean:
                    chapter.word_count = len(clean.split())
        elif agent_name in ("scribe", "editor", "continuity_guardian", "style_curator"):
            saved = strip_em_dashes(normalized_result)

        output_path.write_text(saved, encoding='utf-8')
        print(f"✅ Output saved: {output_path}")

        if resolved_mode == "proposal_only":
            self._persist_canon_proposal(
                chapter_number=chapter_number,
                agent_name=agent_name,
                normalized_response=normalized_result,
                source_path=output_path,
            )
        elif chapter_number is not None:
            self.state.save_state()

        return saved

    def _ensure_directories(self):
        """Create output directories."""
        self.manuscript_dir.mkdir(parents=True, exist_ok=True)
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
    
    # ===== Project Initialization =====
    
    def init_project(self, title: str, genre: str, author: str = "") -> str:
        """Initialize a new novel project."""
        print(f"🎭 Initializing new project: '{title}'")
        print(f"   Genre: {genre}")
        
        # Initialize state
        state = initialize_project(str(self.project_path), title, genre)
        
        if author:
            state.set_metadata('author', author)
        
        # Create project structure
        self._create_project_files(title, genre)
        
        state.save_state()
        
        print(f"✅ Project initialized!")
        print(f"   State file: outputs/state/story_state.json")
        print(f"   Next step: Define your characters and story bible")
        
        return str(self.project_path)
    
    def _create_project_files(self, title: str, genre: str):
        """Create initial project template files."""
        # Create story bible template
        bible_path = self.outputs_dir / "story_bible.md"
        bible_content = f"""# Story Bible: {title}

## 📚 Genre
{genre}

## 🎭 Themes
- [Theme 1]
- [Theme 2]
- [Theme 3]

## 🎨 Tone
[Describe the emotional tone of the story]

## 🌍 Setting

### Time Period
[When does the story take place?]

### Primary Locations
- [Location 1]: [Description]
- [Location 2]: [Description]

### World Rules
[Magic system, technology level, social structures, etc.]

## 📖 Structure
- Target Word Count: 80,000
- Estimated Chapters: 32
- POV: Third Person Limited
- Tense: Past

## 👥 Character Roster
[Link to character profiles]

## 🔗 Plot Overview
[Brief summary of main story]

## 📝 Notes
[Any additional world-building notes]
"""
        bible_path.write_text(bible_content, encoding='utf-8')
        
        # Create character template
        char_template_path = self.templates_dir / "character_profile.md"
        char_template_path.parent.mkdir(parents=True, exist_ok=True)
        char_template = """# Character Profile: [Name]

## Basic Information
- **Full Name**: 
- **Age**: 
- **Role**: [Protagonist/Antagonist/Supporting]
- **Occupation**: 

## Physical Appearance
- Height: 
- Build: 
- Hair: 
- Eyes: 
- Distinguishing Features: 

## Psychology
- **Internal Desire**: [What do they want emotionally?]
- **External Goal**: [What are they trying to achieve?]
- **Fear**: [What terrifies them?]
- **Weakness**: [Character flaw]
- **Strength**: [Key virtue/skill]
- **Secret**: [What are they hiding?]

## Character Arc
- **Starting State**: 
- **Transformation**: 
- **Ending State**: 

## Relationships
- [Character Name]: [Relationship type and dynamics]

## Voice
- **Speech Patterns**: 
- **Vocabulary Level**: 
- **Common Phrases**: 

## Background
[Backstory that shaped them]

## Notes
[Additional details]
"""
        char_template_path.write_text(char_template, encoding='utf-8')
    
    # ===== Character Management =====
    
    def add_character(self, name: str, role: str, **kwargs) -> str:
        """Add a new character to the project."""
        char_id = f"char_{len(self.state.characters) + 1:03d}"
        
        character = Character(
            id=char_id,
            full_name=name,
            role=role,
            **kwargs
        )
        
        self.state.add_character(character)
        self.state.save_state()
        
        print(f"✅ Added character: {name} ({char_id})")
        return char_id
    
    def list_characters(self):
        """List all characters in the project."""
        chars = self.state.get_all_characters()
        
        if not chars:
            print("No characters defined yet.")
            return
        
        print("\n👥 Characters:")
        print("-" * 60)
        for char in chars:
            arc_info = f"{char.arc_stage} ({char.arc_progress}%)"
            print(f"  {char.id}: {char.full_name} ({char.role})")
            print(f"      Arc: {arc_info}")
            if char.current_location:
                print(f"      Location: {char.current_location}")
            print()
    
    # ===== Plot Management =====
    
    def add_plot_thread(self, name: str, description: str, thread_type: str = "main", priority: int = 3) -> str:
        """Add a new plot thread."""
        thread_id = f"plot_{len(self.state.plot_threads) + 1:03d}"
        
        thread = PlotThread(
            id=thread_id,
            name=name,
            description=description,
            thread_type=thread_type,
            priority=priority
        )
        
        self.state.add_plot_thread(thread)
        self.state.save_state()
        
        print(f"✅ Added plot thread: {name} ({thread_id})")
        return thread_id
    
    def list_plot_threads(self):
        """List all plot threads."""
        threads = list(self.state.plot_threads.values())
        
        if not threads:
            print("No plot threads defined yet.")
            return
        
        print("\n🔗 Plot Threads:")
        print("-" * 60)
        for thread in sorted(threads, key=lambda t: -t.priority):
            status_icon = "🟢" if thread.status == "active" else "🔴" if thread.status == "resolved" else "🟡"
            print(f"  {status_icon} [{thread.priority}] {thread.name} ({thread.thread_type})")
            print(f"      Status: {thread.status}")
            print(f"      {thread.description[:80]}...")
            print()
    
    # ===== Planning Phase =====
    
    def plan_outline(self, num_chapters: int = 32, target_words: int = 80000, dry_run: bool = False):
        """Ask the Architect for the book blueprint and persist its scaffold."""
        print("\n🏗️  ARCHITECT: Creating story outline...")
        print(f"   Target: {num_chapters} chapters, ~{target_words:,} words")

        # Keep the machine-readable scaffold for existing API/UI consumers. The
        # Architect's actual reasoning artifact lives beside it as outline.md.
        outline = {
            'metadata': {
                'title': self.state.metadata.get('title', 'Untitled'),
                'genre': self.state.metadata.get('genre', 'Unknown'),
                'target_chapters': num_chapters,
                'target_word_count': target_words,
                'created': datetime.now().isoformat()
            },
            'acts': self._generate_act_structure(num_chapters),
            'chapter_summaries': self._generate_chapter_templates(num_chapters)
        }
        
        # Save outline
        outline_path = self.outputs_dir / "outline.json"
        with open(outline_path, 'w', encoding='utf-8') as f:
            json.dump(outline, f, indent=2)

        raw_prompt_path = self.outputs_dir / "input" / "prompt.md"
        brief_path = self.outputs_dir / "input" / "brief.json"
        raw_prompt = raw_prompt_path.read_text(encoding="utf-8") if raw_prompt_path.exists() else ""
        brief = brief_path.read_text(encoding="utf-8") if brief_path.exists() else "{}"
        architect_prompt = f"""# ARCHITECT TASK: Build the Full Novel Blueprint

Create a complete, causally coherent blueprint for this novel. This is the
authoritative plan that every later chapter outline must follow.

## Targets
- Chapters: {num_chapters}
- Total words: {target_words}
- Genre: {self.state.metadata.get('genre', 'Fiction')}

## Structured Brief
```json
{brief}
```

## Author's Original Prompt
```markdown
{raw_prompt or '[No source prompt was saved; use StoryState metadata.]'}
```

## Required Sections
1. Logline and thematic argument
2. Main cast with external goal, internal need, flaw, secret, and full arc
3. Setting and immutable world rules
4. Main plot and subplot cause-effect chains
5. Three-act beat map with explicit chapter assignments
6. A numbered plan for exactly {num_chapters} chapters; each entry must include
   POV, chapter goal, key conflict, emotional change, continuity obligations,
   planted/payoff threads, and ending hook
7. Assumptions and structural risks

## Machine-Readable Foundation Contract

Before the Markdown analysis, emit exactly one JSON object inside these tags:

```text
[STORY_FOUNDATION_JSON]
{{
  "title": "...",
  "premise": "...",
  "themes": ["..."],
  "setting": {{"time_period": "...", "primary_location": "...", "world_rules": ["..."]}},
  "characters": [
    {{
      "id": "char_001", "name": "...", "role": "protagonist",
      "age": null, "physical_description": "...", "internal_desire": "...",
      "external_goal": "...", "fear": "...", "weakness": "...",
      "strength": "...", "secret": "...", "arc": "..."
    }}
  ],
  "plot_threads": [
    {{
      "id": "plot_001", "name": "...", "description": "...",
      "type": "main", "priority": 5, "resolution_chapter": {num_chapters}
    }}
  ],
  "style": {{"tone": "...", "pov": "third_limited", "tense": "past", "prose_style": "balanced"}},
  "chapters": [
    {{
      "number": 1, "title": "...", "pov": "...", "summary": "...",
      "target_words": {max(1, target_words // num_chapters)}
    }}
  ]
}}
[/STORY_FOUNDATION_JSON]
```

Use exactly {num_chapters} chapter objects with unique numbers 1 through
{num_chapters}. After the closing tag, provide the human-readable blueprint.

Do not write chapter prose. Resolve missing creative details by making explicit,
genre-appropriate assumptions rather than asking questions.
"""
        result = self._run_agent_or_save_prompt(
            agent_name="architect",
            user_prompt=architect_prompt,
            prompt_path=self.outputs_dir / "outline_prompt.md",
            output_path=self.outputs_dir / "outline.md",
            dry_run=dry_run,
            label="Architect designing full-book outline",
        )
        if result is not None:
            try:
                foundation = self._parse_story_foundation(result, num_chapters)
                foundation_path = self.outputs_dir / "input" / "foundation.json"
                foundation_path.parent.mkdir(parents=True, exist_ok=True)
                foundation_path.write_text(
                    json.dumps(foundation, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )
                self._apply_story_foundation(foundation, target_words)
            except (ValueError, json.JSONDecodeError) as exc:
                message = f"Architect output contract failed: {exc}"
                if self.raise_llm_errors:
                    raise LLMError(message) from exc
                print(f"⚠️  {message}")

        print(f"   Outline scaffold: {outline_path}")
        print(f"\n📝 Next: Review outline.json and run 'plan chapter --number 1'")

        return result if result is not None else outline

    @staticmethod
    def _parse_story_foundation(text: str, num_chapters: int) -> Dict[str, Any]:
        match = re.search(
            r"\[STORY_FOUNDATION_JSON\]\s*(\{.*?\})\s*\[/STORY_FOUNDATION_JSON\]",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            raise ValueError("missing [STORY_FOUNDATION_JSON] block")
        data = json.loads(match.group(1))
        if not isinstance(data, dict):
            raise ValueError("story foundation must be a JSON object")
        chapters = data.get("chapters")
        if not isinstance(chapters, list) or len(chapters) != num_chapters:
            raise ValueError(f"story foundation must contain exactly {num_chapters} chapters")
        numbers = sorted(int(chapter.get("number", 0)) for chapter in chapters if isinstance(chapter, dict))
        if numbers != list(range(1, num_chapters + 1)):
            raise ValueError(f"chapter numbers must cover 1 through {num_chapters}")
        if not isinstance(data.get("characters"), list) or not data["characters"]:
            raise ValueError("story foundation must define at least one character")
        if not isinstance(data.get("plot_threads"), list) or not data["plot_threads"]:
            raise ValueError("story foundation must define at least one plot thread")
        return data

    def _apply_story_foundation(self, foundation: Dict[str, Any], target_words: int) -> None:
        """Hydrate StoryState so context packs and continuity checks have canon."""
        if foundation.get("title") and self.state.metadata.get("title") in (None, "", "Untitled"):
            self.state.set_metadata("title", str(foundation["title"]))
        if foundation.get("premise"):
            self.state.set_metadata("premise", str(foundation["premise"]))
            self.state.update_story_bible("premise", str(foundation["premise"]))
        self.state.update_story_bible("themes", list(foundation.get("themes") or []))
        self.state.update_story_bible("setting", dict(foundation.get("setting") or {}))

        for index, raw in enumerate(foundation.get("characters") or [], start=1):
            if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
                continue
            char_id = str(raw.get("id") or f"char_{index:03d}")
            age = raw.get("age")
            try:
                age = int(age) if age is not None else None
            except (TypeError, ValueError):
                age = None
            character = Character(
                id=char_id,
                full_name=str(raw["name"]).strip(),
                role=str(raw.get("role") or "supporting"),
                age=age,
                physical_description=str(raw.get("physical_description") or ""),
                internal_desire=str(raw.get("internal_desire") or ""),
                external_goal=str(raw.get("external_goal") or ""),
                fear=str(raw.get("fear") or ""),
                weakness=str(raw.get("weakness") or ""),
                strength=str(raw.get("strength") or ""),
                secret=str(raw.get("secret") or ""),
                notes=str(raw.get("arc") or raw.get("notes") or ""),
            )
            self.state.characters[char_id] = character

        for index, raw in enumerate(foundation.get("plot_threads") or [], start=1):
            if not isinstance(raw, dict) or not str(raw.get("name") or "").strip():
                continue
            thread_id = str(raw.get("id") or f"plot_{index:03d}")
            try:
                priority = max(1, min(5, int(raw.get("priority", 3))))
            except (TypeError, ValueError):
                priority = 3
            resolution = raw.get("resolution_chapter")
            try:
                resolution = int(resolution) if resolution is not None else None
            except (TypeError, ValueError):
                resolution = None
            self.state.plot_threads[thread_id] = PlotThread(
                id=thread_id,
                name=str(raw["name"]).strip(),
                description=str(raw.get("description") or ""),
                thread_type=str(raw.get("type") or "main"),
                priority=priority,
                start_chapter=int(raw.get("start_chapter") or 1),
                target_resolution_chapter=resolution,
            )

        chapters = foundation.get("chapters") or []
        default_target = max(1, target_words // max(1, len(chapters)))
        for raw in chapters:
            number = int(raw["number"])
            chapter = self.state.get_chapter(number) or self.state.create_chapter(number)
            chapter.title = str(raw.get("title") or f"Chapter {number}")
            chapter.pov_character = str(raw.get("pov") or "")
            summary = str(raw.get("summary") or "").strip()
            if summary and summary not in chapter.plot_advances:
                chapter.plot_advances.append(summary)
            try:
                chapter.target_word_count = max(1, int(raw.get("target_words") or default_target))
            except (TypeError, ValueError):
                chapter.target_word_count = default_target
            chapter.status = "planned"

        style = foundation.get("style") or {}
        if isinstance(style, dict):
            for source, target in (
                ("tone", "tone"),
                ("pov", "point_of_view"),
                ("tense", "tense"),
                ("prose_style", "prose_style"),
            ):
                if style.get(source):
                    setattr(self.state.style_profile, target, str(style[source]))
        self._write_foundation_story_bible(foundation)
        self.state.save_state()

    def _write_foundation_story_bible(self, foundation: Dict[str, Any]) -> None:
        """Render Architect canon into the human-readable story bible."""
        lines = [
            f"# Story Bible: {self.state.metadata.get('title', 'Untitled')}",
            "",
            "## Premise",
            str(foundation.get("premise") or "[Not specified]"),
            "",
            "## Themes",
        ]
        themes = foundation.get("themes") or []
        lines.extend(f"- {theme}" for theme in themes)
        if not themes:
            lines.append("- None specified")

        setting = foundation.get("setting") or {}
        lines.extend(["", "## Setting"])
        if isinstance(setting, dict):
            lines.append(f"- Time period: {setting.get('time_period') or '[Not specified]'}")
            lines.append(f"- Primary location: {setting.get('primary_location') or '[Not specified]'}")
            lines.append("- World rules:")
            rules = setting.get("world_rules") or []
            lines.extend(f"  - {rule}" for rule in rules)
            if not rules:
                lines.append("  - None specified")

        lines.extend(["", "## Characters"])
        for raw in foundation.get("characters") or []:
            lines.extend([
                f"### {raw.get('name', 'Unnamed')} ({raw.get('role', 'supporting')})",
                f"- External goal: {raw.get('external_goal') or '[Not specified]'}",
                f"- Internal desire: {raw.get('internal_desire') or '[Not specified]'}",
                f"- Fear: {raw.get('fear') or '[Not specified]'}",
                f"- Flaw: {raw.get('weakness') or '[Not specified]'}",
                f"- Secret: {raw.get('secret') or '[Not specified]'}",
                f"- Arc: {raw.get('arc') or '[Not specified]'}",
                "",
            ])

        lines.append("## Plot Threads")
        for raw in foundation.get("plot_threads") or []:
            lines.append(
                f"- **{raw.get('name', 'Unnamed')}** ({raw.get('type', 'main')}): "
                f"{raw.get('description') or '[Not specified]'}"
            )

        style = foundation.get("style") or {}
        lines.extend([
            "",
            "## Style",
            f"- Tone: {style.get('tone') or '[Not specified]'}",
            f"- POV: {style.get('pov') or '[Not specified]'}",
            f"- Tense: {style.get('tense') or '[Not specified]'}",
            f"- Prose: {style.get('prose_style') or '[Not specified]'}",
            "",
            "## Source",
            "The original prompt is preserved at `outputs/input/prompt.md`.",
            "",
        ])
        (self.outputs_dir / "story_bible.md").write_text("\n".join(lines), encoding="utf-8")
    
    def _generate_act_structure(self, num_chapters: int) -> List[Dict]:
        """Generate a 3-act structure template."""
        act1_end = int(num_chapters * 0.25)
        act2_end = int(num_chapters * 0.75)
        
        return [
            {
                'act_number': 1,
                'name': 'Setup',
                'chapters': list(range(1, act1_end + 1)),
                'percent': 25,
                'key_beats': [
                    'Opening Image',
                    'Theme Stated',
                    'Setup',
                    'Catalyst',
                    'Debate'
                ]
            },
            {
                'act_number': 2,
                'name': 'Confrontation',
                'chapters': list(range(act1_end + 1, act2_end + 1)),
                'percent': 50,
                'key_beats': [
                    'Break into Two',
                    'B Story',
                    'Fun and Games',
                    'Midpoint',
                    'Bad Guys Close In',
                    'All Is Lost',
                    'Dark Night'
                ]
            },
            {
                'act_number': 3,
                'name': 'Resolution',
                'chapters': list(range(act2_end + 1, num_chapters + 1)),
                'percent': 25,
                'key_beats': [
                    'Break into Three',
                    'Finale',
                    'Final Image'
                ]
            }
        ]
    
    def _generate_chapter_templates(self, num_chapters: int) -> List[Dict]:
        """Generate empty chapter templates."""
        return [
            {
                'number': i,
                'title': f'Chapter {i}',
                'status': 'planned',
                'pov_character': '',
                'summary': '',
                'word_count_target': 2500,
                'scenes': []
            }
            for i in range(1, num_chapters + 1)
        ]
    
    def plan_chapter(self, chapter_number: int, summary: str = "", pov: str = "", dry_run: bool = False):
        """Plan a specific chapter in detail (Architect agent expands the outline)."""
        print(f"\n📋 Planning Chapter {chapter_number}...")

        chapter = self.state.get_chapter(chapter_number)
        if not chapter:
            chapter = self.state.create_chapter(chapter_number)

        if summary:
            chapter.plot_advances.append(summary)
        if pov:
            chapter.pov_character = pov

        chapter.status = 'planned'
        self.state.save_state()

        prompt = self._generate_chapter_outline_prompt(chapter)
        prompt_path = self.outputs_dir / f"chapter_{chapter_number:03d}_prompt.md"
        outline_path = self.outputs_dir / f"chapter_{chapter_number:03d}_outline.md"

        self._run_agent_or_save_prompt(
            agent_name="architect",
            user_prompt=prompt,
            prompt_path=prompt_path,
            output_path=outline_path,
            dry_run=dry_run,
            label="Architect expanding chapter outline",
        )

    def _generate_chapter_outline_prompt(self, chapter: ChapterState) -> str:
        """Prompt the Architect to produce a STRUCTURED BEAT-SHEET (not prose).

        This is deliberately distinct from `_generate_chapter_prompt` (the Scribe's
        drafting prompt). The Architect plans the chapter; the Scribe writes it.
        """
        pack_md = format_context_pack(build_context_pack(self.state, chapter.number, purpose="architect"))
        master_outline_path = self.outputs_dir / "outline.md"
        master_outline = (
            master_outline_path.read_text(encoding="utf-8")[:50000]
            if master_outline_path.exists()
            else "[No full-book outline available]"
        )

        prompt = f"""# ARCHITECT TASK: Outline Chapter {chapter.number}

Produce a structured **beat-sheet outline** for this chapter that the Scribe will
later expand into prose. This is a PLANNING artifact.

## Chapter Information
- **Number**: {chapter.number}
- **Title**: {chapter.title or 'Propose a fitting title'}
- **POV Character**: {chapter.pov_character or '[Specify]'}
- **Target Word Count**: {chapter.target_word_count}

## Story Context
- **Genre**: {self.state.metadata.get('genre', 'Unknown')}
- **Premise**: {self.state.metadata.get('premise') or self.state.story_bible.get('premise') or '[Not provided]'}

## Authoritative Full-Book Outline
{master_outline}

{pack_md}
## Required Output Format

Return ONLY the outline, in this Markdown structure do NOT write any prose,
dialogue, or narrative paragraphs:

```
# Chapter {chapter.number}: <title>

**POV:** {chapter.pov_character or '[POV]'} | **Target:** {chapter.target_word_count} words

## Chapter Goal
<1-2 sentences: what must change by the end of this chapter>

## Beats
1. **<beat name>** <1-2 sentence summary of what happens; the conflict/turn>
2. **<beat name>** <...>
3. **<beat name>** <...>
(4-7 beats total)

## Continuity Notes
- <facts the Scribe must honor: who knows what, locations, timeline>

## Ending Hook
<the line/turn that pulls the reader into the next chapter>
```

Write the beat-sheet now. Outline only no prose.
"""
        return prompt

    # ===== Style Curation =====

    def curate_chapter(self, chapter_number: int, dry_run: bool = False):
        """Run the Style Curator and write a clean candidate-final manuscript."""
        self.last_canon_proposal_ids = []
        resolved_mode = self.state_update_mode
        self._validate_state_update_mode(resolved_mode)
        print(f"\n🎨 CURATING Chapter {chapter_number}...")
        chapter = self.state.get_chapter(chapter_number)
        if not chapter:
            message = f"Chapter {chapter_number} not found"
            if self.raise_llm_errors:
                raise ValueError(message)
            print(f"❌ {message}")
            return None
        if chapter.status not in ("validated", "complete"):
            message = f"Chapter {chapter_number} must be validated before style curation"
            if self.raise_llm_errors:
                raise ValueError(message)
            print(f"❌ {message}")
            return None
        if chapter.continuity_checks.get("status") == "FAIL":
            message = f"Chapter {chapter_number} failed continuity validation"
            if self.raise_llm_errors:
                raise ValueError(message)
            print(f"❌ {message}")
            return None

        source_path = self.manuscript_dir / f"chapter_{chapter_number:03d}_revised.md"
        if not source_path.exists():
            source_path = self.manuscript_dir / f"chapter_{chapter_number:03d}_draft.md"
        if not source_path.exists():
            message = f"No manuscript artifact found for chapter {chapter_number}"
            if self.raise_llm_errors:
                raise ValueError(message)
            print(f"❌ {message}")
            return None

        chapter_text = source_path.read_text(encoding="utf-8")
        prompt = f"""# STYLE CURATOR TASK: Final Polish for Chapter {chapter_number}

Polish this validated chapter while preserving every plot fact, character
decision, clue, reveal, and continuity constraint. Improve voice consistency,
rhythm, diction, dialogue distinction, and genre fit. Do not add new events or
change story outcomes.

## Target Profile
- Genre: {self.state.metadata.get('genre', 'Fiction')}
- Audience: {self.state.metadata.get('audience', '') or '[Not specified]'}
- Language: {self.state.metadata.get('language', 'English')}
- Tone: {self.state.style_profile.tone}
- POV: {self.state.style_profile.point_of_view}
- Prose style: {self.state.style_profile.prose_style}

## Validated Chapter
```markdown
{chapter_text}
```

## Required Output Contract

Return a short `[STYLE_ANALYSIS]` block, then the complete polished manuscript
inside `[REVISED_CHAPTER]...[/REVISED_CHAPTER]`, followed by an optional
`[STYLE_STATE_UPDATE]` block. The revised chapter must be complete, not excerpts
or recommendations.
"""
        prompt_path = self.feedback_dir / f"chapter_{chapter_number:03d}_style_prompt.md"
        report_path = self.feedback_dir / f"chapter_{chapter_number:03d}_style_report.md"
        candidate_path = self.manuscript_dir / f"chapter_{chapter_number:03d}_candidate_final.md"
        prompt_path.write_text(prompt, encoding="utf-8")
        if dry_run:
            print(f"   [dry-run] Prompt saved: {prompt_path}")
            return None

        try:
            llm = self._get_llm("style_curator")
            print(f"   Style Curator polishing ({llm.provider}:{llm.model})...")
            result = llm.run_agent("style_curator", prompt)
        except LLMError as exc:
            print(f"❌ LLM call failed: {exc}")
            print(f"   Prompt saved at {prompt_path} you can run it manually.")
            if self.raise_llm_errors:
                raise
            return None
        if resolved_mode == "proposal_only":
            raw_path = report_path.with_suffix(report_path.suffix + ".raw")
            raw_path.write_text(result, encoding="utf-8")
        normalized_result = normalize_agent_output(result)
        response = normalized_result if resolved_mode == "proposal_only" else result
        match = re.search(
            r"\[REVISED_CHAPTER\](.*?)\[/REVISED_CHAPTER\]",
            response,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            message = "Style Curator response is missing [REVISED_CHAPTER]"
            if self.raise_llm_errors:
                raise LLMError(message)
            print(f"❌ {message}; raw response saved at {report_path}")
            report_path.write_text(strip_em_dashes(response), encoding="utf-8")
            return None
        clean, meta = sanitize_manuscript(match.group(1).strip())
        if not clean:
            message = "Style Curator returned an empty revised chapter"
            if self.raise_llm_errors:
                raise LLMError(message)
            report_path.write_text(strip_em_dashes(response), encoding="utf-8")
            print(f"❌ {message}; raw response saved at {report_path}")
            return None
        report_path.write_text(strip_em_dashes(response), encoding="utf-8")
        if resolved_mode == "legacy_apply":
            ingest_agent_output(self.state, chapter_number, "style_curator", result)
            apply_header_to_chapter(chapter, meta)
            chapter.word_count = len(clean.split())
        candidate_path.write_text(clean, encoding="utf-8")
        if resolved_mode == "proposal_only":
            self._persist_canon_proposal(
                chapter_number=chapter_number,
                agent_name="style_curator",
                normalized_response=normalized_result,
                source_path=candidate_path,
            )
        else:
            self.state.save_state()
        print(f"✅ Candidate final saved: {candidate_path}")
        return clean

    def _generate_chapter_prompt(self, chapter: ChapterState) -> str:
        """Generate a detailed prompt for the Scribe agent."""
        pack_md = format_context_pack(build_context_pack(self.state, chapter.number, purpose="scribe"))

        prompt = f"""# SCRIBE PROMPT: Chapter {chapter.number}

## Chapter Information
- **Number**: {chapter.number}
- **Title**: {chapter.title or 'TBD'}
- **POV Character**: {chapter.pov_character or '[Specify]'}
- **Target Word Count**: {chapter.target_word_count}

## Story Context
- **Genre**: {self.state.metadata.get('genre', 'Unknown')}
- **Premise**: {self.state.metadata.get('premise') or self.state.story_bible.get('premise') or '[Not provided]'}

{pack_md}
## Chapter Goals
- [Primary plot advancement]
- [Character development moment]
- [Emotional beat to hit]

## Writing Requirements
- Maintain deep POV for {chapter.pov_character or '[POV character]'}
- Include at least 3 sensory details
- End with a compelling hook
- Target: {chapter.target_word_count} words

## State Update Requirements
End the response with the required state block. In addition to prose facts,
record only evidence-backed metadata changes using these exact fields:
- Plot_Thread_Updates: `<thread_id> | status=<active|resolved|abandoned|foreshadowed> | milestone=<change> | chapter=<number>`; resolved/abandoned threads are terminal unless `reopen=true` is explicit
- Character_References: `<character_id or full name> | chapter=<number> | note=<reference or documented absence>`
Do not list a referenced/off-page character in Characters_Present.

## Style Profile
- Tone: {self.state.style_profile.tone}
- POV: {self.state.style_profile.point_of_view}
- Style: {self.state.style_profile.prose_style}

---

**Write the complete chapter now. Follow all protocols in your system instructions.**
"""
        return prompt
    
    # ===== Writing Phase =====
    
    def write_chapter(self, chapter_number: int, draft_text: str = "", dry_run: bool = False):
        """Call the Scribe agent to draft the chapter, or accept supplied draft text."""
        self.last_canon_proposal_ids = []
        resolved_mode = self.state_update_mode
        self._validate_state_update_mode(resolved_mode)
        print(f"\n✍️  Writing Chapter {chapter_number}...")

        chapter = self.state.get_chapter(chapter_number)
        if not chapter:
            print(f"❌ Chapter {chapter_number} not found. Run 'plan chapter --number {chapter_number}' first.")
            return

        draft_path = self.manuscript_dir / f"chapter_{chapter_number:03d}_draft.md"

        if draft_text:
            normalized_draft = normalize_agent_output(draft_text)
            clean, meta = sanitize_manuscript(normalized_draft)
            if resolved_mode == "legacy_apply":
                apply_header_to_chapter(chapter, meta)
                chapter.status = 'drafted'
                chapter.word_count = len(clean.split())
            draft_path.write_text(clean, encoding='utf-8')
            print(f"   Draft saved: {draft_path}")
            if resolved_mode == "proposal_only":
                self._persist_canon_proposal(
                    chapter_number=chapter_number,
                    agent_name="scribe",
                    normalized_response=normalized_draft,
                    source_path=draft_path,
                )
            else:
                self.state.save_state()
            return

        # Build Scribe user prompt: prefer expanded outline if present, else fall back.
        outline_path = self.outputs_dir / f"chapter_{chapter_number:03d}_outline.md"
        pack_md = format_context_pack(build_context_pack(self.state, chapter_number, purpose="scribe"))
        if outline_path.exists():
            user_prompt = outline_path.read_text(encoding='utf-8') + "\n\n" + pack_md
        else:
            user_prompt = self._generate_chapter_prompt(chapter)

        prompt_path = self.outputs_dir / f"chapter_{chapter_number:03d}_scribe_prompt.md"
        result = self._run_agent_or_save_prompt(
            agent_name="scribe",
            user_prompt=user_prompt,
            prompt_path=prompt_path,
            output_path=draft_path,
            dry_run=dry_run,
            label="Scribe drafting chapter",
            chapter_number=chapter_number,
        )

        if result is not None and resolved_mode == "legacy_apply":
            chapter.status = 'drafted'
            chapter.word_count = len(result.split())
        if resolved_mode == "legacy_apply":
            self.state.save_state()
    
    def submit_draft(self, chapter_number: int, draft_path: str):
        """Submit a draft file for a chapter (also ingests embedded state-update blocks)."""
        self.last_canon_proposal_ids = []
        resolved_mode = self.state_update_mode
        self._validate_state_update_mode(resolved_mode)
        draft_file = Path(draft_path)
        if not draft_file.exists():
            print(f"❌ Draft file not found: {draft_path}")
            return

        draft_text = draft_file.read_text(encoding='utf-8')
        if resolved_mode == "proposal_only":
            normalized_draft = normalize_agent_output(draft_text)
            clean, _meta = sanitize_manuscript(normalized_draft)
            saved_path = self.manuscript_dir / f"chapter_{chapter_number:03d}_draft.md"
            saved_path.write_text(clean, encoding="utf-8")
            self._persist_canon_proposal(
                chapter_number=chapter_number,
                agent_name="scribe",
                normalized_response=normalized_draft,
                source_path=saved_path,
            )
            print(f"✅ Draft submitted for Chapter {chapter_number}")
            print(f"   Next: Run 'edit chapter --number {chapter_number}'")
            return

        self.write_chapter(chapter_number, draft_text)

        changes = ingest_agent_output(self.state, chapter_number, "scribe", draft_text)
        if changes:
            print(f"   📝 State updates ({len(changes)}):")
            for line in changes:
                print(f"      • {line}")
            self.state.save_state()

        print(f"✅ Draft submitted for Chapter {chapter_number}")
        print(f"   Next: Run 'edit chapter --number {chapter_number}'")
    
    # ===== Editing Phase =====
    
    def edit_chapter(self, chapter_number: int, mode: str = "line", dry_run: bool = False):
        """Call the Editor agent to revise the chapter."""
        self.last_canon_proposal_ids = []
        resolved_mode = self.state_update_mode
        self._validate_state_update_mode(resolved_mode)
        print(f"\n🔍 EDITING Chapter {chapter_number} (Mode: {mode})")

        chapter = self.state.get_chapter(chapter_number)
        if not chapter:
            print(f"❌ Chapter {chapter_number} not found.")
            return
        if chapter.status not in ['drafted', 'editing', 'edited']:
            print(f"❌ Chapter {chapter_number} has no draft to edit.")
            return

        draft_path = self.manuscript_dir / f"chapter_{chapter_number:03d}_draft.md"
        if not draft_path.exists():
            print(f"❌ Draft file not found: {draft_path}")
            return

        draft_text = draft_path.read_text(encoding='utf-8')
        edit_prompt = self._generate_edit_prompt(chapter, draft_text, mode)
        edit_prompt_path = self.feedback_dir / f"chapter_{chapter_number:03d}_edit_prompt.md"
        revised_path = self.manuscript_dir / f"chapter_{chapter_number:03d}_revised.md"

        if resolved_mode == "legacy_apply":
            chapter.status = 'editing'
            self.state.save_state()

        result = self._run_agent_or_save_prompt(
            agent_name="editor",
            user_prompt=edit_prompt,
            prompt_path=edit_prompt_path,
            output_path=revised_path,
            dry_run=dry_run,
            label=f"Editor revising ({mode})",
            chapter_number=chapter_number,
        )

        if result is not None and resolved_mode == "legacy_apply":
            chapter.status = 'edited'
            self.state.save_state()
    
    def _generate_edit_prompt(self, chapter: ChapterState, draft_text: str, mode: str) -> str:
        """Generate an editing prompt."""
        return f"""# EDITOR PROMPT: Chapter {chapter.number}

## Editing Mode: {mode.upper()}

## Chapter Information
- **Number**: {chapter.number}
- **POV**: {chapter.pov_character}
- **Current Word Count**: {chapter.word_count}
- **Target**: {chapter.target_word_count}

## Original Draft

```markdown
{draft_text}
```

## Editing Instructions

### Mode-Specific Focus: {mode}
""" + {
            "line": """
- Fix awkward phrasing
- Strengthen verbs
- Remove filter words (saw, felt, thought, etc.)
- Improve sentence rhythm
- Eliminate wordiness
""",
            "developmental": """
- Verify scene goals are clear
- Check escalation
- Improve transitions between scenes
- Enhance emotional arc
- Strengthen chapter hook
""",
            "pacing": """
- Identify and fix slow sections
- Compress exposition
- Accelerate action sequences
- Vary scene lengths
- Check chapter-ending momentum
""",
            "dialogue": """
- Ensure natural speech patterns
- Add subtext
- Optimize dialogue tags
- Verify distinct character voices
- Remove on-the-nose dialogue
""",
            "tension": """
- Raise stakes where flat
- Add micro-tension
- Strengthen chapter ending
- Create anticipation
- Deepen conflict
"""
        }.get(mode, "- General line editing") + f"""

## Style Profile to Maintain
- Tone: {self.state.style_profile.tone}
- Prose Style: {self.state.style_profile.prose_style}
- POV: {self.state.style_profile.point_of_view}

## Output Format
Provide:
1. EDITOR_ANALYSIS section with issues found
2. REVISED_CHAPTER with the full edited text
3. EDITOR_STATE_UPDATE with changes summary

---

**Edit the chapter now. Follow your system instructions.**
"""
    
    def submit_edit(self, chapter_number: int, edited_path: str):
        """Submit an edited chapter (also ingests embedded editor state-updates)."""
        self.last_canon_proposal_ids = []
        resolved_mode = self.state_update_mode
        self._validate_state_update_mode(resolved_mode)
        edit_file = Path(edited_path)
        if not edit_file.exists():
            print(f"❌ Edit file not found: {edited_path}")
            return

        revised_path = self.manuscript_dir / f"chapter_{chapter_number:03d}_revised.md"
        text = edit_file.read_text(encoding='utf-8')
        if resolved_mode == "proposal_only":
            normalized_text = normalize_agent_output(text)
            match = re.search(
                r"\[REVISED(?:_|\s+)CHAPTER\](.*?)\[/REVISED(?:_|\s+)CHAPTER\]",
                normalized_text,
                re.IGNORECASE | re.DOTALL,
            )
            manuscript_text = match.group(1).strip() if match else normalized_text
            clean, _meta = sanitize_manuscript(manuscript_text)
            revised_path.write_text(clean, encoding="utf-8")
            self._persist_canon_proposal(
                chapter_number=chapter_number,
                agent_name="editor",
                normalized_response=normalized_text,
                source_path=revised_path,
            )
            print(f"✅ Edit submitted for Chapter {chapter_number}")
            print(f"   Next: Run 'validate chapter --number {chapter_number}'")
            return

        import shutil
        shutil.copy(edit_file, revised_path)

        changes = ingest_agent_output(self.state, chapter_number, "editor", text)

        chapter = self.state.get_chapter(chapter_number)
        if chapter:
            chapter.status = 'edited'

        if changes:
            print(f"   📝 State updates ({len(changes)}):")
            for line in changes:
                print(f"      • {line}")
        self.state.save_state()

        print(f"✅ Edit submitted for Chapter {chapter_number}")
        print(f"   Next: Run 'validate chapter --number {chapter_number}'")
    
    # ===== Validation Phase =====
    
    def validate_chapter(self, chapter_number: int, dry_run: bool = False):
        """Call the Continuity Guardian agent to validate the chapter."""
        self.last_canon_proposal_ids = []
        resolved_mode = self.state_update_mode
        self._validate_state_update_mode(resolved_mode)
        print(f"\n🛡️  VALIDATING Chapter {chapter_number}...")

        chapter = self.state.get_chapter(chapter_number)
        if not chapter:
            print(f"❌ Chapter {chapter_number} not found.")
            return

        for suffix in ['_revised', '_draft']:
            text_path = self.manuscript_dir / f"chapter_{chapter_number:03d}{suffix}.md"
            if text_path.exists():
                chapter_text = text_path.read_text(encoding='utf-8')
                break
        else:
            print(f"❌ No chapter file found.")
            return

        # Deterministic pre-check runs free, gives the Guardian a head start.
        findings = run_continuity_checks(self.state, self.project_path, as_of_chapter=chapter_number)
        if findings:
            print(f"   🔬 Pre-check: {len(findings)} deterministic finding(s)")
            # Persist into chapter state and surface to the Guardian via the prompt.
            if resolved_mode == "legacy_apply":
                chapter.continuity_checks['pre_check_findings'] = [f.to_dict() for f in findings]

        validation_prompt = self._generate_validation_prompt(chapter_number, chapter_text)
        if findings:
            validation_prompt = to_context_block(findings) + "\n" + validation_prompt
        validation_prompt_path = self.feedback_dir / f"chapter_{chapter_number:03d}_validation_prompt.md"
        report_path = self.feedback_dir / f"chapter_{chapter_number:03d}_continuity_report.md"

        result = self._run_agent_or_save_prompt(
            agent_name="continuity_guardian",
            user_prompt=validation_prompt,
            prompt_path=validation_prompt_path,
            output_path=report_path,
            dry_run=dry_run,
            label="Continuity Guardian validating",
            chapter_number=chapter_number,
        )

        if result is not None and resolved_mode == "legacy_apply":
            chapter.status = 'validated'
            self.state.save_state()
    
    def _generate_validation_prompt(self, chapter_number: int, chapter_text: str) -> str:
        """Generate a validation prompt."""
        context = self.state.get_continuity_context(chapter_number)
        body = slice_chapter_for_llm(chapter_text)
        pack_md = format_context_pack(
            build_context_pack(
                self.state, chapter_number, purpose="guardian", chapter_text=chapter_text,
            )
        )

        prompt = f"""# CONTINUITY GUARDIAN PROMPT: Chapter {chapter_number}

## Chapter Text to Validate

```markdown
{body}
```

## Current Story State

{pack_md}

### Character Positions
"""
        for char_id, location in context['character_locations'].items():
            char = self.state.get_character(char_id)
            if char:
                prompt += f"- **{char.full_name}**: {location or 'Unknown'}\n"

        prompt += "\n### Character Emotional States\n"
        for char_id, state in context['character_emotional_states'].items():
            char = self.state.get_character(char_id)
            if char:
                prompt += f"- **{char.full_name}**: {state or 'Unknown'}\n"

        prompt += (
            "\nValidate the chapter against the Context pack / Codex above. Flag contradictions "
            "with named characters, locations, world rules, items, and "
            "relationship labels as Critical or Warning. Do not invent Codex facts "
            "that are not listed.\n"
        )

        prompt += f"""
### Previous Chapter Events
[Check against Chapter {chapter_number - 1} events and Prior chapters in the pack]

## Story Bible Reference
- Genre: {self.state.metadata.get('genre', 'Unknown')}
- World Rules: [Reference story_bible.md and Codex world rules above]

## Validation Tasks

### Character Continuity
- [ ] Actions align with established personality
- [ ] Knowledge matches what they should know
- [ ] Skills/capabilities remain consistent
- [ ] Relationships reflect prior development and Codex edges

### Timeline Continuity
- [ ] Events occur in logical sequence
- [ ] Time references are consistent
- [ ] Travel times are realistic

### World Consistency
- [ ] Magic/tech rules followed
- [ ] Setting details match prior descriptions and Codex locations
- [ ] Named items/places match Codex entries
- [ ] Hostile/ally bonds match on-page behavior

### Plot Continuity
- [ ] Foreshadowing acknowledged or advanced
- [ ] No dropped plot threads (unless intentional)
- [ ] Cause-effect chains intact

## Output Format

```
[CONTINUITY_REPORT]
Chapter: {chapter_number}
Status: [PASS / WARNING / FAIL]

Critical_Issues: [List with suggested fixes]
Warnings: [List with suggested fixes]
New_Facts_Established: [List]
Plot_Thread_Updates:
  - <thread_id> | status=<active|resolved|abandoned|foreshadowed> | milestone=<what changed> | chapter=<number>
Character_References:
  - <character_id or full name> | chapter=<number> | note=<reference or documented off-page absence>
Foreshadowing_Resolved:
  - id=chN:fsM | note=<payoff>
[CONTINUITY_REPORT]
```

---

**Validate this chapter now. Follow your system instructions.**
"""
        return prompt
    
    def approve_chapter(self, chapter_number: int):
        """Mark a chapter as complete and surface accumulated state changes."""
        chapter = self.state.get_chapter(chapter_number)
        if not chapter:
            print(f"❌ Chapter {chapter_number} not found.")
            return

        # Gate: don't approve if continuity validation failed
        cstatus = chapter.continuity_checks.get('status')
        if cstatus == 'FAIL':
            issues = chapter.continuity_checks.get('critical_issues', [])
            print(f"❌ Cannot approve Continuity Guardian reported FAIL with {len(issues)} critical issue(s).")
            print("   Resolve or override by editing the chapter, then re-validate.")
            return

        chapter.status = 'complete'
        self.state.save_state()

        print(f"✅ Chapter {chapter_number} approved.")
        if chapter.plot_advances:
            print(f"   Events: {len(chapter.plot_advances)}")
        if chapter.foreshadowing_planted:
            print(f"   Foreshadowing planted: {len(chapter.foreshadowing_planted)}")
        if chapter.foreshadowing_resolved:
            print(f"   Foreshadowing resolved: {len(chapter.foreshadowing_resolved)}")
        if chapter.quality_scores:
            scored = ", ".join(f"{k}={v}" for k, v in chapter.quality_scores.items())
            print(f"   Scores: {scored}")
        if cstatus:
            print(f"   Continuity: {cstatus}")

        completed = len(self.state.get_completed_chapters())
        total = len(self.state.chapters)
        if total:
            print(f"\n📊 Progress: {completed}/{total} chapters complete ({completed/total*100:.1f}%)")
    
    # ===== Continuity Engine =====

    def run_checks(self, chapter_number: Optional[int] = None) -> int:
        """Run the deterministic continuity engine. Returns count of critical findings."""
        findings = run_continuity_checks(self.state, self.project_path, as_of_chapter=chapter_number)
        print(summarize_findings(findings))
        critical = sum(1 for f in findings if f.severity == "critical")
        return critical

    # ===== Status and Reporting =====
    
    def status(self):
        """Show project status."""
        print("\n" + "=" * 60)
        print(f"📖 {self.state.metadata.get('title', 'Untitled')}")
        print(f"   Genre: {self.state.metadata.get('genre', 'Unknown')}")
        print(f"   Created: {self.state.metadata.get('created', 'Unknown')}")
        print("=" * 60)
        
        print("\n👥 Characters:", len(self.state.characters))
        print("🔗 Plot Threads:", len(self.state.plot_threads))
        print("   Active:", len(self.state.get_active_plot_threads()))
        
        print("\n📝 Chapters:")
        chapters_by_status = {}
        for ch in self.state.chapters.values():
            status = ch.status
            chapters_by_status[status] = chapters_by_status.get(status, 0) + 1
        
        for status, count in sorted(chapters_by_status.items()):
            icon = {
                'planned': '⚪',
                'drafting': '🟡',
                'drafted': '🟠',
                'editing': '🔵',
                'edited': '🟣',
                'validated': '🟢',
                'complete': '✅'
            }.get(status, '⚪')
            print(f"   {icon} {status.capitalize()}: {count}")
        
        completed = len(self.state.get_completed_chapters())
        total = len(self.state.chapters)
        if total > 0:
            print(f"\n📊 Progress: {completed}/{total} ({completed/total*100:.1f}%)")
        
        print("\n" + "=" * 60)
    
    def export(self, format: str = "markdown"):
        """Export the manuscript."""
        print(f"\n📤 Exporting manuscript as {format}...")
        
        completed_chapters = sorted(
            self.state.get_completed_chapters(),
            key=lambda c: c.number
        )
        
        if format == "markdown":
            output_lines = [
                f"# {self.state.metadata.get('title', 'Untitled')}",
                "",
                f"*{self.state.metadata.get('genre', 'Fiction')}*",
                "",
                "---",
                ""
            ]
            
            for chapter in completed_chapters:
                # Find the chapter file
                for suffix in ['_revised', '_draft']:
                    ch_path = self.manuscript_dir / f"chapter_{chapter.number:03d}{suffix}.md"
                    if ch_path.exists():
                        content = ch_path.read_text(encoding='utf-8')
                        output_lines.append(content)
                        output_lines.append("\n\n---\n\n")
                        break
            
            output_path = self.outputs_dir / f"{self.state.metadata.get('title', 'manuscript').replace(' ', '_')}.md"
            output_path.write_text('\n'.join(output_lines), encoding='utf-8')
            
            print(f"✅ Exported: {output_path}")
        
        # TODO: Add other formats (docx, pdf, etc.)


def _provider_configured() -> bool:
    """True if LLMClient can resolve a provider from the environment."""
    try:
        LLMClient()
        return True
    except LLMError:
        return False


def _is_dry_run(args) -> bool:
    """Dry-run commands only save prompts, so they don't need a live LLM."""
    return bool(getattr(args, 'dry_run', False))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Novel OS - Multi-Agent Fiction Writing Framework",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s init --title "My Novel" --genre "Thriller"
  %(prog)s character add --name "John Doe" --role protagonist
  %(prog)s plan outline --chapters 32
  %(prog)s plan chapter --number 1
  %(prog)s write --chapter 1
  %(prog)s edit --chapter 1 --mode line
  %(prog)s validate --chapter 1
  %(prog)s approve --chapter 1
  %(prog)s run --project ./projects/my-novel --prompt ./prompt.md --approval auto
  %(prog)s resume --project ./projects/my-novel --run-id RUN_ID --approval auto
  %(prog)s status
  %(prog)s export --format markdown
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Init command
    init_parser = subparsers.add_parser('init', help='Initialize a new project')
    init_parser.add_argument('--title', required=True, help='Novel title')
    init_parser.add_argument('--genre', required=True, help='Primary genre')
    init_parser.add_argument('--author', default='', help='Author name')
    
    # Character commands
    char_parser = subparsers.add_parser('character', help='Character management')
    char_subparsers = char_parser.add_subparsers(dest='char_command')
    
    char_add = char_subparsers.add_parser('add', help='Add a character')
    char_add.add_argument('--name', required=True, help='Character full name')
    char_add.add_argument('--role', required=True, choices=['protagonist', 'antagonist', 'supporting', 'minor'])
    
    char_list = char_subparsers.add_parser('list', help='List characters')
    
    # Plot commands
    plot_parser = subparsers.add_parser('plot', help='Plot thread management')
    plot_subparsers = plot_parser.add_subparsers(dest='plot_command')
    
    plot_add = plot_subparsers.add_parser('add', help='Add a plot thread')
    plot_add.add_argument('--name', required=True, help='Thread name')
    plot_add.add_argument('--description', required=True, help='Thread description')
    plot_add.add_argument('--type', default='main', choices=['main', 'subplot', 'character_arc', 'mystery'])
    plot_add.add_argument('--priority', type=int, default=3, help='Priority 1-5')
    
    plot_list = plot_subparsers.add_parser('list', help='List plot threads')
    
    # Plan commands
    plan_parser = subparsers.add_parser('plan', help='Planning phase')
    plan_subparsers = plan_parser.add_subparsers(dest='plan_command')
    
    plan_outline = plan_subparsers.add_parser('outline', help='Create story outline')
    plan_outline.add_argument('--chapters', type=int, default=32, help='Number of chapters')
    plan_outline.add_argument('--words', type=int, default=80000, help='Target word count')
    plan_outline.add_argument('--dry-run', action='store_true', help='Save prompt only, do not call LLM')
    
    plan_chapter = plan_subparsers.add_parser('chapter', help='Plan specific chapter')
    plan_chapter.add_argument('--number', type=int, required=True, help='Chapter number')
    plan_chapter.add_argument('--summary', default='', help='Chapter summary')
    plan_chapter.add_argument('--pov', default='', help='POV character')
    plan_chapter.add_argument('--dry-run', action='store_true', help='Save prompt only, do not call LLM')

    # Write command
    write_parser = subparsers.add_parser('write', help='Writing phase')
    write_parser.add_argument('--chapter', type=int, required=True, help='Chapter number')
    write_parser.add_argument('--draft-file', default='', help='Path to draft file')
    write_parser.add_argument('--dry-run', action='store_true', help='Save prompt only, do not call LLM')

    # Edit command
    edit_parser = subparsers.add_parser('edit', help='Editing phase')
    edit_parser.add_argument('--chapter', type=int, required=True, help='Chapter number')
    edit_parser.add_argument('--mode', default='line',
                            choices=['line', 'developmental', 'pacing', 'dialogue', 'tension'],
                            help='Editing mode')
    edit_parser.add_argument('--edited-file', default='', help='Path to edited file')
    edit_parser.add_argument('--dry-run', action='store_true', help='Save prompt only, do not call LLM')

    # Validate command
    validate_parser = subparsers.add_parser('validate', help='Validation phase')
    validate_parser.add_argument('--chapter', type=int, required=True, help='Chapter number')
    validate_parser.add_argument('--dry-run', action='store_true', help='Save prompt only, do not call LLM')

    # Style Curator command
    curate_parser = subparsers.add_parser('curate', help='Style-polish a validated chapter')
    curate_parser.add_argument('--chapter', type=int, required=True, help='Chapter number')
    curate_parser.add_argument('--dry-run', action='store_true', help='Save prompt only, do not call LLM')
    
    # Approve command
    approve_parser = subparsers.add_parser('approve', help='Approve chapter')
    approve_parser.add_argument('--chapter', type=int, required=True, help='Chapter number')
    
    # Check command deterministic continuity engine
    check_parser = subparsers.add_parser('check', help='Run deterministic continuity checks (no LLM)')
    check_parser.add_argument('--chapter', type=int, default=None,
                              help='Evaluate as-of this chapter (default: latest drafted)')

    # Status command
    status_parser = subparsers.add_parser('status', help='Show project status')
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export manuscript')
    export_parser.add_argument('--format', default='markdown', choices=['markdown', 'docx'],
                              help='Export format')

    # Setup wizard configure which LLM Novel OS talks to.
    subparsers.add_parser('setup', help='Configure your LLM provider (interactive)')

    # Full-book pipeline commands. These use PipelineRunner directly and leave
    # the existing single-stage commands intact for manual workflows.
    run_parser = subparsers.add_parser('run', help='Run a prompt through the complete book pipeline')
    run_parser.add_argument('--project', required=True, help='Novel project directory')
    run_parser.add_argument('--prompt', required=True, help='UTF-8 prompt file, or - for stdin')
    run_parser.add_argument('--title', default='', help='Override title inferred from the prompt')
    run_parser.add_argument('--genre', default='', help='Override genre inferred from the prompt')
    run_parser.add_argument('--author', default='', help='Author/byline')
    run_parser.add_argument('--pov', default='', help='Default point of view')
    run_parser.add_argument('--chapters', type=int, default=None,
                            help='Target chapter count (default: infer from prompt)')
    run_parser.add_argument('--words', type=int, default=None,
                            help='Target total word count (default: infer from prompt)')
    run_parser.add_argument('--edit-mode', default='line',
                            choices=['line', 'developmental', 'pacing', 'dialogue', 'tension'])
    run_parser.add_argument('--approval', default='review_required',
                            choices=['review_required', 'auto'],
                            help='Pause for review, or explicitly auto-promote candidate finals')
    run_parser.add_argument('--max-retries', type=int, default=2)
    run_parser.add_argument('--retry-backoff', type=float, default=2.0,
                            help='Initial retry delay in seconds (exponential, capped at 30s)')
    run_parser.add_argument('--output', nargs='+', default=['markdown'],
                            choices=['markdown', 'html', 'docx', 'epub'])
    run_parser.add_argument('--model', default='', help='Override NOVEL_OS_MODEL for this process')
    run_parser.add_argument('--dry-run', action='store_true',
                            help='Persist prompt and brief only; do not call an LLM')

    resume_parser = subparsers.add_parser('resume', help='Resume a persisted full-book run')
    resume_parser.add_argument('--project', required=True, help='Novel project directory')
    resume_parser.add_argument('--run-id', required=True)
    resume_parser.add_argument('--approval', choices=['review_required', 'auto'], default=None)
    resume_parser.add_argument('--approve-chapter', type=int, default=None,
                               help='Record human approval for one waiting candidate and continue')

    run_status_parser = subparsers.add_parser('run-status', help='Inspect a persisted full-book run')
    run_status_parser.add_argument('--project', required=True, help='Novel project directory')
    run_status_parser.add_argument('--run-id', required=True)

    retry_parser = subparsers.add_parser('retry', help='Retry the current failed or blocked pipeline stage')
    retry_parser.add_argument('--project', required=True, help='Novel project directory')
    retry_parser.add_argument('--run-id', required=True)
    retry_parser.add_argument('--phase', required=True)
    retry_parser.add_argument('--chapter', type=int, default=None)
    retry_parser.add_argument('--approval', choices=['review_required', 'auto'], default=None)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return

    # Setup wizard runs without an orchestrator (it configures the LLM itself).
    if args.command == 'setup':
        from setup_wizard import run_wizard
        sys.exit(run_wizard())

    # Commands that need a working LLM. If none resolves, offer the wizard first.
    _LLM_COMMANDS = {'plan', 'write', 'edit', 'validate', 'curate', 'run'}
    if args.command in _LLM_COMMANDS and not _is_dry_run(args):
        if not _provider_configured():
            print("No LLM provider is configured yet.")
            answer = input("Run the setup wizard now? [Y/n]: ").strip().lower() or "y"
            if answer == "y":
                from setup_wizard import run_wizard
                if run_wizard() != 0:
                    sys.exit(1)
            else:
                print("You can configure later with: python -m core.orchestrator setup")
                sys.exit(1)

    if args.command in {'run', 'resume', 'run-status', 'retry'}:
        from pipeline_models import RunSpec
        from pipeline_runner import PipelineRunner

        runner = PipelineRunner(getattr(args, 'project', None))
        try:
            if args.command == 'run':
                if args.model:
                    os.environ['NOVEL_OS_MODEL'] = args.model
                spec = RunSpec(
                    project_path=args.project,
                    prompt_path=args.prompt,
                    num_chapters=args.chapters,
                    target_words=args.words,
                    title=args.title,
                    genre=args.genre,
                    author=args.author,
                    pov=args.pov,
                    edit_mode=args.edit_mode,
                    approval_policy=args.approval,
                    max_retries=args.max_retries,
                    retry_backoff_seconds=args.retry_backoff,
                    output_formats=tuple(args.output),
                    dry_run=args.dry_run,
                    model=args.model,
                )
                manifest = runner.run(spec)
            elif args.command == 'resume':
                manifest = runner.resume(
                    args.run_id,
                    args.project,
                    approval_policy=args.approval,
                    approve_chapter=args.approve_chapter,
                )
            elif args.command == 'retry':
                manifest = runner.retry(
                    args.run_id,
                    phase=args.phase,
                    chapter=args.chapter,
                    project_path=args.project,
                    approval_policy=args.approval,
                )
            else:
                manifest = runner.inspect(args.run_id, args.project)
        except (FileNotFoundError, ValueError) as exc:
            print(f"❌ {exc}")
            return 1

        print(json.dumps({
            'run_id': manifest.run_id,
            'status': manifest.status,
            'phase': manifest.current_phase,
            'chapter': manifest.current_chapter,
            'error': manifest.error,
        }, indent=2, ensure_ascii=False))
        if args.command == 'run-status':
            return 0
        return 0 if manifest.status == 'completed' else 2

    # Initialize orchestrator
    orchestrator = NovelOrchestrator()

    # Route commands
    if args.command == 'init':
        orchestrator.init_project(args.title, args.genre, args.author)
    
    elif args.command == 'character':
        if args.char_command == 'add':
            orchestrator.add_character(args.name, args.role)
        elif args.char_command == 'list':
            orchestrator.list_characters()
        else:
            char_parser.print_help()
    
    elif args.command == 'plot':
        if args.plot_command == 'add':
            orchestrator.add_plot_thread(args.name, args.description, args.type, args.priority)
        elif args.plot_command == 'list':
            orchestrator.list_plot_threads()
        else:
            plot_parser.print_help()
    
    elif args.command == 'plan':
        if args.plan_command == 'outline':
            orchestrator.plan_outline(args.chapters, args.words, dry_run=args.dry_run)
        elif args.plan_command == 'chapter':
            orchestrator.plan_chapter(args.number, args.summary, args.pov, dry_run=args.dry_run)
        else:
            plan_parser.print_help()

    elif args.command == 'write':
        if args.draft_file:
            orchestrator.submit_draft(args.chapter, args.draft_file)
        else:
            orchestrator.write_chapter(args.chapter, dry_run=args.dry_run)

    elif args.command == 'edit':
        if args.edited_file:
            orchestrator.submit_edit(args.chapter, args.edited_file)
        else:
            orchestrator.edit_chapter(args.chapter, args.mode, dry_run=args.dry_run)

    elif args.command == 'validate':
        orchestrator.validate_chapter(args.chapter, dry_run=args.dry_run)

    elif args.command == 'curate':
        orchestrator.curate_chapter(args.chapter, dry_run=args.dry_run)
    
    elif args.command == 'approve':
        orchestrator.approve_chapter(args.chapter)
    
    elif args.command == 'check':
        critical = orchestrator.run_checks(args.chapter)
        sys.exit(1 if critical else 0)

    elif args.command == 'status':
        orchestrator.status()
    
    elif args.command == 'export':
        orchestrator.export(args.format)


if __name__ == '__main__':
    sys.exit(main() or 0)
