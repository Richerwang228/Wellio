import {test,expect,type Page} from '@playwright/test'
import {createFixture} from '../../src/lib/fixtures'

const png=Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5xkAAAAASUVORK5CYII=','base64')
const maxBytes=10*1024*1024
declare global {interface Window {attachmentTestURLs:{created:string[];revoked:string[]}}}

async function prepare(page:Page,locale:'en'|'zh-CN'){
 let uploads=0
 const errors:string[]=[]
 page.on('pageerror',error=>errors.push(error.message))
 await page.setViewportSize({width:390,height:844})
 await page.addInitScript(locale=>{
  localStorage.setItem('wellio.locale.v1',locale)
  window.attachmentTestURLs={created:[],revoked:[]}
  const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL)
  URL.createObjectURL=blob=>{const url=create(blob);window.attachmentTestURLs.created.push(url);return url}
  URL.revokeObjectURL=url=>{window.attachmentTestURLs.revoked.push(url);revoke(url)}
 },locale)
 await page.route('**/api/state',route=>route.fulfill({json:createFixture()}))
 await page.route('**/api/attachments',route=>{uploads++;return route.fulfill({status:500,json:{errorCode:'UNEXPECTED_UPLOAD'}})})
 await page.goto('/agent?preview=0')
 await expect(page.locator('.agent-page')).toBeVisible()
 await expect(page.locator('html')).toHaveAttribute('lang',locale)
 return {uploads:()=>uploads,errors}
}

for(const locale of ['en','zh-CN'] as const)test(`${locale}: invalid selections preserve the current image and draft without uploading`,async({page})=>{
 const observed=await prepare(page,locale)
 const input=page.locator('.agent-file-input'),draft=page.locator('.agent-page textarea'),preview=page.locator('.agent-pending-attachment img')
 await expect(input).toHaveAttribute('accept','image/jpeg,image/png,image/webp')
 const text=locale==='en'?'Please log this meal after I review it.':'等我确认内容后帮我记录这餐。'
 await draft.fill(text)
 const before=await page.evaluate(()=>window.attachmentTestURLs.created.length)
 await input.setInputFiles({name:'original.png',mimeType:'image/png',buffer:png})
 await expect(page.locator('.agent-pending-attachment')).toContainText('original.png')
 await expect(preview).toBeVisible()
 await expect.poll(()=>preview.evaluate(image=>image instanceof HTMLImageElement&&image.complete&&image.naturalWidth>0)).toBe(true)
 const originalURL=await preview.getAttribute('src')
 const formatMessage=locale==='en'?'Choose a JPEG, PNG or WebP image.':'请选择 JPEG、PNG 或 WebP 图片。'
 const sizeMessage=locale==='en'?'Choose an image of 10 MB or less.':'请选择不超过 10 MB 的图片。'

 // A browser image format outside the server contract must be rejected as well.
 await input.setInputFiles({name:'unsupported.gif',mimeType:'image/gif',buffer:Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7','base64')})
 await expect(page.getByRole('alert')).toHaveText(formatMessage)
 await expect(preview).toHaveAttribute('src',originalURL!)
 await expect(draft).toHaveValue(text)
 expect(await page.evaluate(()=>window.attachmentTestURLs.created.length)).toBe(before+1)

 await input.setInputFiles({name:'too-large.png',mimeType:'image/png',buffer:Buffer.concat([png,Buffer.alloc(maxBytes+1-png.length)])})
 await expect(page.getByRole('alert')).toHaveText(sizeMessage)
 await expect(page.locator('.agent-pending-attachment')).toContainText('original.png')
 await expect(preview).toHaveAttribute('src',originalURL!)
 await expect(draft).toHaveValue(text)
 expect(await page.evaluate(()=>window.attachmentTestURLs.created.length)).toBe(before+1)
 expect(observed.uploads()).toBe(0)

 await input.setInputFiles({name:'replacement.png',mimeType:'image/png',buffer:png})
 await expect(page.locator('.agent-pending-attachment')).toContainText('replacement.png')
 await expect(page.getByRole('alert')).toHaveCount(0)
 const replacementURL=await preview.getAttribute('src')
 expect(replacementURL).not.toBe(originalURL)
 await expect.poll(()=>page.evaluate(url=>window.attachmentTestURLs.revoked.includes(url!),originalURL)).toBe(true)
 await page.getByRole('button',{name:locale==='en'?'Remove image':'移除图片',exact:true}).click()
 await expect(page.locator('.agent-pending-attachment')).toHaveCount(0)
 await expect(draft).toHaveValue(text)
 await expect.poll(()=>page.evaluate(url=>window.attachmentTestURLs.revoked.includes(url!),replacementURL)).toBe(true)
 expect(await page.evaluate(()=>window.attachmentTestURLs.created.length)).toBe(before+2)
 expect(observed.uploads()).toBe(0)
 expect(observed.errors).toEqual([])
})

test('an image exactly at the 10 MiB limit is accepted',async({page})=>{
 const observed=await prepare(page,'en')
 await page.locator('.agent-page textarea').fill('Keep this draft')
 await page.locator('.agent-file-input').setInputFiles({name:'at-limit.png',mimeType:'image/png',buffer:Buffer.concat([png,Buffer.alloc(maxBytes-png.length)])})
 await expect(page.locator('.agent-pending-attachment')).toContainText('at-limit.png')
 await expect(page.getByRole('alert')).toHaveCount(0)
 await expect(page.locator('.agent-page textarea')).toHaveValue('Keep this draft')
 expect(observed.uploads()).toBe(0)
 expect(observed.errors).toEqual([])
})
