import {defineConfig} from '@playwright/test';
export default defineConfig({
  testDir:'./deployment-e2e', outputDir:'./test-results/deployment',
  timeout:120000, workers:1, reporter:'list',
  use:{baseURL:process.env.DEPLOYMENT_URL || 'http://localhost:8080',
    channel:process.platform==='win32'?'msedge':undefined,trace:'off'}
});
