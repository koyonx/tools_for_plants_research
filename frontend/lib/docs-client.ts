// Client-safe docs helpers.  Keep this file free of `node:fs` so it
// can be imported from client components without dragging server-only
// code into the browser bundle.

export function slugToHref(slug: string[]): string {
  if (slug.length === 1 && slug[0] === "index") return "/dashboard/docs";
  return `/dashboard/docs/${slug.join("/")}`;
}
