import { Uploader } from "@/components/Uploader";

export default function UploadPage() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">画像をアップロード</h1>
      <p className="text-sm text-neutral-500">
        1 枚ずつアップロードします。スケール設定と解析は次のページで行います。
      </p>
      <Uploader />
    </div>
  );
}
