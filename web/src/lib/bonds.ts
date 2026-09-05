/** Relationship labels offered in the Add Relationship form. */
export const BOND_OPTIONS = [
  { value: "ally", label: "盟友" },
  { value: "rival", label: "对手" },
  { value: "family", label: "家人" },
  { value: "romantic", label: "恋人" },
  { value: "mentor", label: "导师" },
  { value: "enemy", label: "敌人" },
  { value: "owes debt", label: "亏欠" },
  { value: "secret", label: "秘密关系" },
  { value: "unknown", label: "未知" },
  { value: "other" as const, label: "其他" },
];
