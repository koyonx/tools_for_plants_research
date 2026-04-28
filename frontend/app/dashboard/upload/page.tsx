import { Uploader } from "@/components/Uploader";

type SearchParams = { next?: string };

export default function UploadPage({ searchParams }: { searchParams: SearchParams }) {
  const afterUpload = searchParams.next === "annotate" ? "annotate" : "detail";
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">画像をアップロード</h1>
      <p className="text-sm text-neutral-500">
        {afterUpload === "annotate"
          ? "アップロード後、アノテーション画面に直接戻ります。"
          : "1 枚ずつアップロードします。スケール設定と解析は次のページで行います。"}
      </p>
      <Uploader afterUpload={afterUpload} />
    </div>
  );
}
