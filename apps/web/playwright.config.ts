import { defineConfig } from '@playwright/test'
const output = '../../' + (process.env.TRACECAUSE_BROWSER_ARTIFACTS ?? 'reports/phase15-browser')
export default defineConfig({
  testDir: './e2e', globalSetup:'./e2e/ready.ts', timeout: 90000, workers: 1, fullyParallel: false,
  outputDir: output + '/artifacts',
  reporter: [['list'], ['json', { outputFile: output + '/results.json' }]],
  use: { baseURL: 'http://127.0.0.1:15173', viewport: { width:1440, height:1000 }, trace:'off', screenshot:'only-on-failure' },
})
