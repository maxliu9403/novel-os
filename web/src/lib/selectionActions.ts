/**
 * The one list of things you can do to a selection (design spec §4.6).
 *
 * Novel OS had accumulated three ways in - a right-click menu, a floating
 * bubble that was written but never wired, and a chat box - each offering a
 * slightly different set. That is how a product ends up needing tutorials.
 *
 * Defining the actions once means the bar and the context menu cannot drift
 * apart, and a writer who learns one has learned both.
 */

import type { IconName } from "../components/Icon";

export type SelectionActionId =
  | "rewrite"
  | "expand"
  | "comment"
  | "link"
  | "create"
  | "ask";

export type SelectionAction = {
  id: SelectionActionId;
  label: string;
  icon: IconName;
  /** Shown in the bar; the rest live behind the right-click menu. */
  primary?: boolean;
  /** Actions that operate on the selected words need one to exist. */
  needsSelection: boolean;
};

/**
 * Pure data, with no handlers attached: each surface maps an id to behaviour
 * itself. That keeps the vocabulary in one place while leaving the editor's
 * live selection where it belongs, inside the component that owns the editor.
 *
 * Order is deliberate: the two that change prose come first, because they are
 * why a writer selected anything, and both return the same three-part answer -
 * the proposal as tracked changes, what it breaks, what it might mean.
 */
export const SELECTION_ACTIONS: readonly SelectionAction[] = [
  { id: "rewrite", label: "改写", icon: "sparkles", primary: true, needsSelection: true },
  { id: "expand", label: "扩写", icon: "pen-line", primary: true, needsSelection: true },
  { id: "comment", label: "批注", icon: "message-square", primary: true, needsSelection: true },
  { id: "link", label: "关联到设定库", icon: "users", needsSelection: true },
  { id: "create", label: "新建设定库条目", icon: "plus", needsSelection: true },
  { id: "ask", label: "询问执笔者…", icon: "bot", needsSelection: false },
];

/** The subset shown in the floating bar - the rest stay one right-click away. */
export const BAR_ACTIONS: readonly SelectionAction[] =
  SELECTION_ACTIONS.filter((a) => a.primary);
