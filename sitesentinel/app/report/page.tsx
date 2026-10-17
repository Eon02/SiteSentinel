import { Suspense } from "react";
import ReportForm from "@/components/ReportForm";
import { Skeleton } from "@/components/Skeleton";

// ReportForm reads ?site= with useSearchParams, which needs a Suspense boundary.
export default function ReportPage() {
  return (
    <Suspense
      fallback={
        <div className="mx-auto flex w-full max-w-xl flex-col gap-4 p-4">
          <Skeleton className="h-10" />
          <Skeleton className="h-12" />
          <Skeleton className="h-64" />
        </div>
      }
    >
      <ReportForm />
    </Suspense>
  );
}
