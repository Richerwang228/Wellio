import {test,expect} from '@playwright/test'
import type {Snapshot} from '../../src/lib/contracts'

// No page.route interception: these checks use the production server and SQLite.
test('Profile language is saved by the server without resetting the session',async({page})=>{
 const before=await (await page.request.get('/api/state')).json() as Snapshot
 expect(before.capabilities.persistence).toBe('server')
 await page.goto('/profile')
 const saved=page.waitForResponse(response=>response.url().endsWith('/api/actions')&&response.request().postDataJSON()?.kind==='set_locale')
 await page.getByRole('button',{name:'Chinese',exact:true}).click()
 expect((await saved).status()).toBe(200)
 await expect(page.locator('html')).toHaveAttribute('lang','zh-CN')
 const after=await (await page.request.get('/api/state')).json() as Snapshot
 expect(after.locale).toBe('zh-CN');expect(after.sessionId).toBe(before.sessionId);expect(after.resetEpoch).toBe(before.resetEpoch)
 expect(after.meals).toEqual(before.meals);expect(after.workout).toEqual(before.workout)
 await page.reload();await expect(page.locator('html')).toHaveAttribute('lang','zh-CN')
 // Removing this browser preference proves server restoration as well.
 await page.evaluate(()=>localStorage.removeItem('wellio.locale.v1'))
 await page.reload();await expect(page.locator('html')).toHaveAttribute('lang','zh-CN')
})

test('a scenario reset affects only its own authenticated browser session',async({page,browser})=>{
 const other=await browser.newContext({baseURL:'http://127.0.0.1:3101'})
 try{
  const second=await (await other.request.get('/api/state')).json() as Snapshot
  const first=await (await page.request.get('/api/state')).json() as Snapshot
  expect(first.sessionId).not.toBe(second.sessionId)
  await page.goto('/profile');await page.getByRole('button',{name:'Other settings',exact:true}).click()
  await page.getByLabel('Recovery scenario',{exact:true}).selectOption('low_recovery')
  await page.getByRole('button',{name:'Confirm reset',exact:true}).click()
  await expect(page).toHaveURL(/\/agent$/)
  const changed=await (await page.request.get('/api/state')).json() as Snapshot
  expect(changed.resetEpoch).toBe(first.resetEpoch+1);expect(changed.scenario).toBe('low_recovery');expect(changed.readiness.score).toBe(42)
  const unchanged=await (await other.request.get('/api/state')).json() as Snapshot
  expect(unchanged).toEqual(second)
 }finally{await other.close()}
})

test('a rejected cookie can be cleared and recovered with the visible retry action',async({page,context})=>{
 await context.addCookies([{name:'wellio_session',value:'invalid-signature',url:'http://127.0.0.1:3101'}])
 await page.goto('/agent')
 await expect(page.getByRole('button',{name:'Try again',exact:true})).toBeVisible()
 await page.getByRole('button',{name:'Try again',exact:true}).click()
 await expect(page.locator('.agent-page')).toBeVisible()
 const state=await (await page.request.get('/api/state')).json() as Snapshot
 expect(state.sessionId).toBeTruthy();expect(state.capabilities.persistence).toBe('server')
 expect((await context.cookies()).find(cookie=>cookie.name==='wellio_session')?.value).not.toBe('invalid-signature')
})

test('real training progress survives language change, early finish and reload',async({page})=>{
 await page.setViewportSize({width:390,height:844})
 const before=await (await page.request.get('/api/state')).json() as Snapshot
 await page.goto('/today');await page.getByRole('button',{name:'Start workout',exact:true}).click()
 await expect(page).toHaveURL(/\/workout$/);await page.getByRole('button',{name:'Complete exercise',exact:true}).click()
 await expect(page.getByText('1/3 exercises complete',{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'Back to Today',exact:true}).click();await page.getByRole('link',{name:'Profile',exact:true}).click()
 const languageSave=page.waitForResponse(r=>r.url().endsWith('/api/actions')&&r.request().postDataJSON()?.kind==='set_locale')
 await page.getByRole('button',{name:'Chinese',exact:true}).click();expect((await languageSave).status()).toBe(200)
 await page.getByRole('link',{name:'今日',exact:true}).click();await page.getByRole('button',{name:'继续训练',exact:true}).click()
 await expect(page.getByText('1/3 个动作已完成',{exact:true})).toBeVisible()
 await page.getByRole('button',{name:'结束训练',exact:true}).click();await page.getByLabel('实际训练时长（分钟）',{exact:true}).fill('20')
 await page.getByRole('button',{name:'保存并结束',exact:true}).click();await expect(page.getByRole('heading',{name:'训练已保存',exact:true})).toBeVisible()
 await page.reload();await expect(page.getByRole('heading',{name:'训练已保存',exact:true})).toBeVisible()
 const saved=await (await page.request.get('/api/state')).json() as Snapshot
 expect(saved.workout?.id).toBe(before.workout?.id);expect(saved.workout?.status).toBe('completed');expect(saved.workout?.actualMinutes).toBe(20)
 expect(saved.workout?.exercises.map(e=>e.completed)).toEqual([true,false,false]);expect(saved.history.load).toEqual(before.history.load)
 expect(saved.history.training.filter(row=>row.workoutId===saved.workout?.id)).toHaveLength(1)
})
