import {test,expect} from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('production gateway serves the complete review and download workflow',async({page})=>{
  // Keep the real administrator credential out of DOM snapshots and traces.
  const password=process.env.TEST_ADMIN_PASSWORD || fs.readFileSync(path.resolve('../secrets/initial-password.txt'),'utf8').trim();
  const login=await page.request.post('/api/auth/login',{data:{password},headers:{Origin:new URL(process.env.DEPLOYMENT_URL || 'http://localhost:8080').origin}});
  expect(login.status()).toBe(200);
  const failures:string[]=[];page.on('pageerror',error=>failures.push(error.message));
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Document workspace'})).toBeVisible();
  const filename=`live-browser-${Date.now()}.txt`;
  await page.locator('input[type=file]').setInputFiles({name:filename,mimeType:'text/plain',
    buffer:Buffer.from('Email: redaction@example.com\nPUBLIC INFORMATION')});
  await expect(page.getByRole('heading',{name:'Redaction review'})).toBeVisible({timeout:90000});
  await page.getByRole('button',{name:'Accept all pending'}).click();
  await page.getByRole('button',{name:'Save draft',exact:true}).click();
  await expect(page.getByRole('button',{name:'Save draft',exact:true})).toBeDisabled();
  await page.screenshot({path:'test-results/deployment/editor-live.png',fullPage:true});
  await page.getByRole('button',{name:'Prepare final preview'}).click();
  await page.getByRole('button',{name:'Review final PDF'}).click({timeout:90000});
  const image=page.getByRole('img',{name:'Final PDF page 1'});
  await expect.poll(()=>image.evaluate((img:HTMLImageElement)=>img.complete&&img.naturalWidth>0)).toBe(true);
  await page.getByRole('button',{name:'Confirm reviewed export'}).click();
  const download=page.waitForEvent('download');
  await page.getByRole('link',{name:'Download PDF',exact:true}).last().click();
  expect((await download).suggestedFilename()).toMatch(/^redacted-.*\.pdf$/);
  await page.getByRole('button',{name:'Close final preview'}).click();
  await page.setViewportSize({width:390,height:844});
  await page.screenshot({path:'test-results/deployment/editor-live-mobile.png',fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  expect(failures).toEqual([]);
  await page.getByRole('button',{name:'Delete',exact:true}).click();
  await page.getByRole('button',{name:'Delete permanently'}).click();
  await expect(page.getByRole('heading',{name:'Your next safe-to-share document starts here.'})).toBeVisible();
  await page.getByRole('button',{name:'Sign out',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Welcome back.'})).toBeVisible();
});
