import Link from "next/link";

export default function NotFound() {
  return (
    <main className="mx-auto max-w-md px-5 py-20">
      <h1 className="text-2xl font-semibold">Page not found</h1>
      <p className="mt-2 text-ink-soft">That page does not exist. <Link className="underline" href="/">Go to home</Link>.</p>
    </main>
  );
}
