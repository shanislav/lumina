/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  images: {
    // Posters come already sized from TMDB (w500) / Wikimedia; the browser loads them straight from
    // their CDN. Optimizing them through the Next.js server added nothing but a failure point: with
    // many results at once its fetches to image.tmdb.org timed out and posters were missing.
    unoptimized: true,
    remotePatterns: [
      { protocol: "https", hostname: "image.tmdb.org" },
      // posters of films TMDB does not know (Wikidata / Wikipedia)
      { protocol: "https", hostname: "commons.wikimedia.org" },
      { protocol: "https", hostname: "upload.wikimedia.org" },
    ],
  },
};

module.exports = nextConfig;
