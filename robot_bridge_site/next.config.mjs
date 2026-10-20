/** @type {import('next').NextConfig} */
const nextConfig = {
  allowedDevOrigins: ["10.36.113.172", "10.208.131.172", "10.97.173.172", "localhost:3000", "127.0.0.1:3000"],
  async rewrites() {
    return [
      {
        source: "/instruct",
        destination: "/api/instruct",
      },
      {
        source: "/bio_data",
        destination: "/api/bio_data",
      },
      {
        source: "/pi_instructions",
        destination: "/api/pi_instructions",
      },
      {
        source: "/raw",
        destination: "/api/db?format=text",
      },
      {
        source: "/prompt",
        destination: "/gemini",
      },
    ];
  },
};

export default nextConfig;
