import { requireUser } from "@/lib/auth";
import { Nav } from "@/components/nav";

export default async function DashboardLayout({ children }: { children: React.ReactNode }) {
  const user = await requireUser();
  return (
    <div className="min-h-screen bg-zinc-950 text-zinc-100">
      <Nav user={user} />
      {children}
    </div>
  );
}
