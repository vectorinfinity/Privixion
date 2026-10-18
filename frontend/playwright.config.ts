import { defineConfig } from '@playwright/test';
import path from 'node:path';
const python = process.env.TEST_PYTHON || (process.platform === 'win32' ? path.resolve('../.venv/Scripts/python.exe') : 'python3');
export default defineConfig({
  testDir: './e2e', timeout: 60000, workers: 1, reporter: 'list',
  use: {baseURL: 'http://127.0.0.1:5173', channel: process.platform === 'win32' ? 'msedge' : undefined, trace: 'retain-on-failure'},
  webServer: [
    {command: `"${python}" ../backend/tests/browser_server.py`, env: {PRIVIXION_BROWSER_TEST:'1'}, url: 'http://127.0.0.1:8000/api/health', reuseExistingServer:false,timeout:30000},
    {command:'npm run dev -- --port 5173 --strictPort',url:'http://127.0.0.1:5173',reuseExistingServer:false,timeout:30000}
  ]
});
