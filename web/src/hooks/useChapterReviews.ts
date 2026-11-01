import { useEffect, useState } from "react";
import { readerReviewsApi } from "../api/readerReviews";

/** A single lightweight index per chapter tab, never one request per row. */
export function useChapterReviews(projectId: string) {
  const [result, setResult] = useState<{ projectId: string; chapters: Record<number, string> } | null>(null);
  useEffect(() => {
    let active = true;
    void readerReviewsApi.list(projectId).then(({ reviews }) => {
      const chapters: Record<number, string> = {};
      for (const review of reviews) if (review.report_ready && review.status === "completed") {
        for (const chapter of review.reviewed_chapters ?? []) chapters[chapter] ??= review.id;
      }
      if (active) setResult({ projectId, chapters });
    }).catch(() => { /* Review history availability never prevents reading/editing chapters. */ });
    return () => { active = false; };
  }, [projectId]);
  return result?.projectId === projectId ? result.chapters : {};
}
