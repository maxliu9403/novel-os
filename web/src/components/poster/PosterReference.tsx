import { useState } from "react";
import {
  posterApi,
  posterAssetUrl,
  type PosterReference as Reference,
} from "../../api/poster";

export default function PosterReference({
  projectId,
  value,
  disabled,
  onChanged,
}: {
  projectId: string;
  value: Reference | null;
  disabled: boolean;
  onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function change(file?: File, mode?: Reference["mode"], remove = false) {
    setBusy(true);
    setError("");
    try {
      if (file) {
        if (file.size > 12 * 1024 * 1024)
          throw new Error("图片不得超过 12 MiB");
        await posterApi.uploadReference(projectId, file);
      } else
        await posterApi.selectReference(
          projectId,
          remove ? null : value!.asset_id,
          mode ?? value!.mode,
        );
      await onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="poster-reference">
      <div>
        <h3>
          参考图片 <small>可选</small>
        </h3>
        <p>结合新故事拆解构图、人物关系与视觉表达。</p>
      </div>
      {value && (
        <div className="poster-reference__body">
          <img src={posterAssetUrl(value.url)} alt="本次封面参考图" />
          <div>
            <strong>
              {value.asset.origin === "source_book"
                ? "来源小说封面"
                : "上传的参考图"}
            </strong>
            <p>
              {value.asset.width} × {value.asset.height}
            </p>
            <label>
              参考方式
              <select
                disabled={disabled || busy}
                value={value.mode}
                onChange={(e) =>
                  void change(undefined, e.target.value as Reference["mode"])
                }
              >
                <option value="visual_inspiration">
                  借鉴视觉设计，创作新封面
                </option>
                <option value="adaptation_reference">基于原图二创</option>
              </select>
            </label>
            <button
              type="button"
              className="poster-text-button"
              disabled={disabled || busy}
              onClick={() => void change(undefined, undefined, true)}
            >
              移除参考
            </button>
          </div>
        </div>
      )}
      <label className="poster-button">
        {busy ? "正在保存…" : value ? "替换图片" : "上传参考图片"}
        <input
          hidden
          type="file"
          accept="image/png,image/jpeg"
          disabled={disabled || busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void change(file);
            e.target.value = "";
          }}
        />
      </label>
      <small>PNG / JPEG · 12 MiB 以内</small>
      {error && (
        <p className="poster-error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
