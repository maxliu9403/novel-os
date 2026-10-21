import { useEffect, useState } from "react";
import { posterApi, posterAssetUrl, type PosterCandidate, type PosterLayout, type PosterSet } from "../../api/poster";
import PosterDialog from "./PosterDialog";

const editableFields = new Set(["lines", "font_id", "position", "region", "font_size_min", "font_size_max", "color", "align", "line_spacing", "letter_spacing", "line_styles", "backdrop", "stroke", "shadow", "max_lines", "safe_zones", "font_sha256", "fonts"]);
const fonts = [{id: "serif-bold", label: "经典衬线"}, {id: "sans-bold", label: "现代无衬线"}, {id: "cjk-sans", label: "中文无衬线"}] as const;
export default function PosterTypography({projectId, set, candidate, title, busy, onClose, onSave}: {
  projectId: string; set: PosterSet; candidate: PosterCandidate; title: string; busy: boolean;
  onClose: () => void; onSave: (layout: PosterLayout) => Promise<boolean>;
}) {
  const concept = set.concepts.find(item => item.concept_id === candidate.concept_id);
  const recipe = concept?.scene_plan?._poster_recipe as {title_layout?: PosterLayout} | undefined;
  const [layout, setLayout] = useState<PosterLayout>(() => ({
    position: "bottom", font_id: /[\u3400-\u9fff]/.test(title) ? "cjk-sans" : "serif-bold", font_size_min: .018, font_size_max: .09,
    color: "#fff4df", align: "center", line_spacing: .12, letter_spacing: 0,
    backdrop: {color: "#071326", opacity: .4},
    ...Object.fromEntries(Object.entries({...recipe?.title_layout, ...candidate.poster_metadata?.title_layout}).filter(([field]) => editableFields.has(field))),
  }));
  const [preview, setPreview] = useState<{url: string; layoutKey: string} | null>(null);
  const [error, setError] = useState("");
  const key = JSON.stringify(layout);
  useEffect(() => {
    const controller = new AbortController();
    let url = "";
    let mounted = true;
    const timer = window.setTimeout(() => {
      void posterApi.typographyPreview(projectId, set.cover_set_id, candidate.candidate_id, layout, controller.signal).then(blob => {
        if (!mounted) return;
        url = URL.createObjectURL(blob); setPreview({url, layoutKey: key}); setError("");
      }).catch(reason => { if (mounted && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : "排版预览无法读取"); });
    }, 350);
    return () => { mounted = false; window.clearTimeout(timer); controller.abort(); if (url) URL.revokeObjectURL(url); };
  }, [projectId, set.cover_set_id, candidate.candidate_id, layout, key]);
  const valid = preview?.layoutKey === key && !error;
  const update = (patch: Partial<PosterLayout>) => setLayout(value => {
    const fontChanged = "font_id" in patch || "line_styles" in patch || "lines" in patch;
    // Explicit text edits repin the requested face. Position/effect edits retain
    // the exact frozen font bytes, including historical artwork recipes.
    const next = {...value, ...patch};
    if (fontChanged) {
      delete next.font_sha256; delete next.fonts;
      if (!next.shadow) next.shadow = {color: "#000000", opacity: 0, blur: 0, offset_x: 0, offset_y: 0};
    }
    return next;
  });
  const lines = layout.lines || [title];
  const styleLine = (index: number, patch: NonNullable<PosterLayout["line_styles"]>[number]) => {
    const next = lines.map((_, line) => ({...layout.line_styles?.[line]}));
    next[index] = {...next[index], ...patch}; update({line_styles: next});
  };
  return <PosterDialog title="标题排版" onClose={onClose} wide>
    <div className="poster-editor">
      <div className="poster-editor__preview">
        <img src={preview?.url || posterAssetUrl(candidate.url)} alt={`${title} 排版预览`} />
        <span className="poster-caption">{valid ? "与最终导出使用相同排版引擎" : error ? "保留上次预览，请调整设置" : "正在更新本地排版预览…"}</span>
        {valid && <div className="poster-thumbnail"><img src={preview.url} alt="120 × 180 缩略图检查" /><span>120 × 180 缩略图</span></div>}
      </div>
      <div className="poster-editor__controls">
        <p className="poster-muted">调整标题会保存为独立版本。原画稿、原封面和当前选择保留；此操作不调用图片模型。</p>
        <label>标题区域<select value={layout.position || "bottom"} onChange={event => update({position: event.target.value as PosterLayout["position"], region: undefined})}><option value="top">上方</option><option value="middle">中间</option><option value="bottom">下方</option></select></label>
        <div className="poster-fields"><label>字体<select value={layout.font_id} onChange={event => update({font_id: event.target.value as PosterLayout["font_id"], line_styles: layout.line_styles?.map(style => ({...style, font_id: event.target.value as PosterLayout["font_id"]}))})}>{fonts.map(font => <option key={font.id} value={font.id}>{font.label}</option>)}</select></label><label>对齐<select value={layout.align} onChange={event => update({align: event.target.value as PosterLayout["align"]})}><option value="left">左对齐</option><option value="center">居中</option><option value="right">右对齐</option></select></label></div>
        <label>标题字号上限 <output>{Math.round((layout.font_size_max || .09) * 100)}%</output><input type="range" min="0.045" max="0.16" step="0.005" value={layout.font_size_max || .09} onChange={event => update({font_size_max: Number(event.target.value)})} /><small>相对画稿宽度；引擎在区域内适配完整书名。</small></label>
        <div className="poster-fields"><label>标题颜色<input type="color" value={layout.color || "#fff4df"} onChange={event => update({color: event.target.value, line_styles: layout.line_styles?.map(style => ({...style, color: event.target.value}))})} /></label><label>底衬浓度<input type="range" min="0" max="0.9" step="0.05" value={layout.backdrop?.opacity || 0} onChange={event => update({backdrop: {color: "#071326", ...layout.backdrop, opacity: Number(event.target.value)}})} /></label></div>
        <details className="poster-details"><summary>描边、阴影与背景</summary>
          <div className="poster-fields"><label className="poster-check"><input type="checkbox" checked={Boolean(layout.stroke?.width)} onChange={event => update({stroke: {color: layout.stroke?.color || "#17213f", width: event.target.checked ? .0015 : 0}})} />轻描边</label><label className="poster-check"><input type="checkbox" checked={Boolean(layout.shadow?.opacity)} onChange={event => update({shadow: {color: layout.shadow?.color || "#000000", blur: layout.shadow?.blur || .003, offset_x: layout.shadow?.offset_x ?? 0, offset_y: layout.shadow?.offset_y || .002, opacity: event.target.checked ? .35 : 0}})} />柔和阴影</label></div>
          <div className="poster-fields"><label>背景样式<select value={layout.backdrop?.mode || "solid"} onChange={event => update({backdrop: {color: "#071326", opacity: 0, ...layout.backdrop, mode: event.target.value as "solid" | "gradient"}})}><option value="solid">均匀底衬</option><option value="gradient">渐变底衬</option></select></label>{layout.backdrop?.mode === "gradient" && <label>渐变浓色位置<select value={layout.backdrop.direction || "bottom"} onChange={event => update({backdrop: {...layout.backdrop!, direction: event.target.value as NonNullable<PosterLayout["backdrop"]>["direction"]}})}><option value="top">上方</option><option value="bottom">下方</option><option value="left">左侧</option><option value="right">右侧</option></select></label>}</div>
          <p className="poster-muted">用于增加文字与画面的分离度，效果同步到预览和导出。</p>
        </details>
        <details className="poster-details"><summary>换行与文字层级</summary>
          <label>完整书名（只调整换行，保持词序）<textarea value={lines.join("\n")} onChange={event => update({lines: event.target.value.split("\n"), line_styles: []})} rows={4} /></label>
          <p className="poster-muted">不删除或改写书名。空行、缺字或越界会显示具体提示。</p>
          {lines.map((line, index) => <div className="poster-type-line" key={index}><span>{line || "空行"}</span><label>字号比例<input aria-label={`第 ${index + 1} 行字号比例`} type="number" min="0.5" max="2" step="0.1" value={layout.line_styles?.[index]?.scale || 1} onChange={event => styleLine(index, {scale: Number(event.target.value)})} /></label><label>字体<select aria-label={`第 ${index + 1} 行字体`} value={layout.line_styles?.[index]?.font_id || layout.font_id} onChange={event => styleLine(index, {font_id: event.target.value as PosterLayout["font_id"]})}>{fonts.map(font => <option key={font.id} value={font.id}>{font.label}</option>)}</select></label><label>颜色<input aria-label={`第 ${index + 1} 行颜色`} type="color" value={layout.line_styles?.[index]?.color || layout.color || "#fff4df"} onChange={event => styleLine(index, {color: event.target.value})} /></label></div>)}
          <label>行间距<input type="range" min="0" max="0.7" step="0.05" value={layout.line_spacing || 0} onChange={event => update({line_spacing: Number(event.target.value)})} /></label>
          <label>字间距<input type="range" min="0" max="0.02" step="0.001" value={layout.letter_spacing || 0} onChange={event => update({letter_spacing: Number(event.target.value)})} /></label>
        </details>
        {error && <p className="poster-error" role="alert">{error}</p>}
        <div className="poster-editor__footer"><button type="button" className="poster-button poster-button--primary" disabled={busy || !valid} onClick={async () => { if (await onSave(layout)) onClose(); }}>{busy ? "正在保存版本…" : "保存为新封面版本"}</button><small>保存后可在候选中单独选用。</small></div>
      </div>
    </div>
  </PosterDialog>;
}
