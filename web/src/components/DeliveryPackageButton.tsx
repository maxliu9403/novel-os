import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, type ProjectDeliverySummary } from "../api/client";
import { posterApi } from "../api/poster";
import "../styles/delivery-package.css";

interface Props {
  projectId: string;
  delivery?: ProjectDeliverySummary;
  onChanged?: () => void | Promise<void>;
  compact?: boolean;
}

export default function DeliveryPackageButton(props: Props) {
  return <DeliveryAction key={props.projectId} {...props} />;
}

function DeliveryAction({ projectId, delivery, onChanged, compact }: Props) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [updated, setUpdated] = useState<{ source: string; value: ProjectDeliverySummary }>();
  const source = JSON.stringify(delivery);
  const current = updated && updated.source === source ? updated.value : delivery;
  const lock = useRef(false);
  const alive = useRef(true);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; controller.current?.abort(); };
  }, []);

  async function run() {
    if (lock.current || !current) return;
    lock.current = true;
    setBusy(true); setError("");
    try {
      if (current.status !== "ready") {
        const result = await posterApi.rebuildDelivery(projectId);
        if (!alive.current) return;
        setUpdated({ source, value: result.delivery });
        if (result.delivery.status !== "ready") setError(result.delivery.error || "交付包尚未就绪，请查看交付状态。");
      } else {
        controller.current = new AbortController();
        const response = await fetch(api.deliveryPackageUrl(projectId), { signal: controller.current.signal, cache: "no-store" });
        if (!response.ok) {
          const message = response.status === 409 ? "交付包已发生变化，请更新交付包后再下载。" : response.status === 404 ? "交付包尚未生成，请前往费用与交付查看。" : `交付包下载失败（HTTP ${response.status}）`;
          throw new Error(message);
        }
        const blob = await response.blob();
        const bytes = new Uint8Array(await blob.slice(0, 4).arrayBuffer());
        if (!/application\/(zip|octet-stream)/i.test(response.headers.get("Content-Type") || "") ||
          bytes[0] !== 0x50 || bytes[1] !== 0x4b || bytes[2] !== 0x03 || bytes[3] !== 0x04) {
          throw new Error("服务器未返回有效 ZIP，未下载该文件。");
        }
        if (!alive.current) return;
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = url; anchor.download = "book-package.zip";
        document.body.appendChild(anchor); anchor.click(); anchor.remove();
        window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
      }
    } catch (cause) {
      if (alive.current && !(cause instanceof DOMException && cause.name === "AbortError")) {
        setError(cause instanceof Error ? cause.message : "交付操作失败，请重试。");
      }
    } finally {
      lock.current = false;
      if (alive.current) {
        setBusy(false);
        // Refreshing a card is advisory; a refresh failure must not turn a valid download into an error.
        try { await onChanged?.(); } catch { /* Parent owns refresh errors. */ }
      }
    }
  }

  const canRebuild = (current?.status === "stale" || current?.status === "failed") && current.retryable;
  const label = busy ? (current?.status === "ready" ? "下载中…" : "更新交付包…") : current?.status === "ready" ? "导出交付包" : current?.status === "pending" ? "交付包更新中" : canRebuild ? "更新交付包" : "查看交付";
  return <span className={`delivery-package${compact ? " delivery-package--compact" : ""}`}>
    {current?.status === "ready" || canRebuild || current?.status === "pending" || busy
      ? <button type="button" onClick={() => void run()} disabled={busy || current?.status === "pending"} title={current?.error || undefined}>{label}</button>
      : <Link to={`/projects/${encodeURIComponent(projectId)}?tab=delivery`} title={current?.error || "查看交付包及编译状态"}>{label}</Link>}
    {error && <span role="alert" className="delivery-package__error">{error}</span>}
  </span>;
}
