/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  images: {
    // Thumbnails come straight from i.ytimg.com already sized for a grid, and
    // rendered previews are plain <video> tags. Skipping the optimizer keeps
    // `sharp` out of the runtime entirely.
    unoptimized: true,
  },
};

export default nextConfig;
