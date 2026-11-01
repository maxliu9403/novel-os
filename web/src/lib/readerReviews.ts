export const reviewDate = (seconds: number) => new Date(seconds * 1000).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const reviewMoney = (amount: number | null) => amount === null ? "费用待核算" : `¥${amount.toLocaleString("zh-CN", { maximumFractionDigits: 4 })}`;
