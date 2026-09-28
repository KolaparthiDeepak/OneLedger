import { cookies } from "next/headers";
import { redirect } from "next/navigation";
import { Shell } from "@/components/shell";

export const dynamic = "force-dynamic";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const jar = await cookies();
  if (!jar.get("ol_session") && !jar.get("__Host-ol_session")) redirect("/login");
  return <Shell>{children}</Shell>;
}
