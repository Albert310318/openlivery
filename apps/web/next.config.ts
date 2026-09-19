import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: process.cwd(),
  experimental: {
    useTypeScriptCli: false,
  },
};

export default nextConfig;
