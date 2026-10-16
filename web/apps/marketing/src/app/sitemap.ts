import type { MetadataRoute } from "next";

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    { url: "https://openbower.ai", lastModified: now },
    { url: "https://openbower.ai/privacy", lastModified: now },
    { url: "https://openbower.ai/terms", lastModified: now },
  ];
}
