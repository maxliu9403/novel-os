import type { AudienceChannel, CreativeSummary } from "../api/creativeTypes";

export const AUDIENCE_LABELS: Record<AudienceChannel, string> = { female: "女频", male: "男频" };
export const AUDIENCE_CHANNELS: AudienceChannel[] = ["female", "male"];

export function supportsAudience(item: CreativeSummary, channel: AudienceChannel): boolean {
  return item.status === "enabled" && item.audience_ready === true &&
    item.audience_channels?.includes(channel) === true;
}

export function audienceLabel(channels?: AudienceChannel[]): string {
  return channels?.length ? channels.map(channel => AUDIENCE_LABELS[channel]).join(" / ") : "待补频道指导";
}
