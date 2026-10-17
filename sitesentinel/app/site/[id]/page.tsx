import { Suspense } from "react";
import SiteDetail, { SiteDetailSkeleton } from "@/components/SiteDetail";

// params is request-time data under Cache Components, so read it inside Suspense.
export default function SitePage({ params }: PageProps<"/site/[id]">) {
  return (
    <Suspense fallback={<SiteDetailSkeleton />}>
      <SiteDetailFromParams params={params} />
    </Suspense>
  );
}

async function SiteDetailFromParams({ params }: { params: PageProps<"/site/[id]">["params"] }) {
  const { id } = await params;
  return <SiteDetail id={id} />;
}
