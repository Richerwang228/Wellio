import {test,expect,type Page} from '@playwright/test'
import {createFixture} from '../../src/lib/fixtures'
import type {ActionRequest,Snapshot,Proposal} from '../../src/lib/contracts'
async function transport(page:Page,initial:Snapshot){let state=structuredClone(initial);const actions:ActionRequest[]=[]
 await page.route('**/api/state',route=>route.fulfill({json:state}))
 await page.route('**/api/actions',async route=>{const input=route.request().postDataJSON() as ActionRequest;actions.push(input)
  if(input.kind==='start_workout'&&state.workout){state.workout.status='in_progress';state.workout.version++}
  if(input.kind==='complete_exercise'&&state.workout){state.workout.exercises.find(e=>e.id===input.exerciseId)!.completed=true;state.workout.version++}
  if(input.kind==='apply_proposal'){const p=state.proposals.find(p=>p.id===input.proposalId)!;p.status='applied';if(p.scope==='schedule'){state.plan.restDates=[state.dayKey];for(const move of p.moves||[]){const s=state.plan.sessions.find(s=>s.id===move.sessionId)!;s.date=move.to}state.plan.version++}else if(p.workout){state.workout=p.workout;if(input.startAfterApply)state.workout.status='in_progress'}}
  state.revision++;await route.fulfill({json:{status:'succeeded',requestId:input.requestId,snapshot:state,applyStatus:input.kind==='apply_proposal'?'succeeded':undefined,startStatus:input.kind==='apply_proposal'?(input.startAfterApply?'succeeded':'not_requested'):undefined}})
 });return actions}
function proposal(state:Snapshot,scope:'workout'|'schedule'):Proposal{return {id:'test-proposal',scope,status:'pending',reason:{en:'A shorter session fits today.', 'zh-CN':'今天适合更短的训练。'},expected:{meal:1,plan:1,workout:1,conditions:1,readiness:1},contextReadId:'test-context',readinessSnapshotId:state.readiness.id,...(scope==='workout'?{workout:{...state.workout!,estimatedMinutes:25,exercises:state.workout!.exercises.slice(0,2)}}:{restDate:state.dayKey,moves:state.plan.sessions.map((s,i)=>({sessionId:s.id,split:s.split,from:s.date,to:['2026-09-14','2026-09-16','2026-09-18'][i]}))})}}
for(const width of [320,390])for(const locale of ['en','zh-CN'] as const)test(`${width}px ${locale}: routes, language and navigation remain usable`,async({page})=>{
 await page.setViewportSize({width,height:width===320?640:844});await page.addInitScript(l=>{if(!localStorage.getItem('wellio.locale.v1'))localStorage.setItem('wellio.locale.v1',l)},locale);await transport(page,createFixture())
 const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message))
 for(const route of ['/today','/agent','/trends','/profile']){await page.goto(route);await expect(page.locator('.app-loading')).toHaveCount(0);await expect(page.locator('.app-nav')).toBeVisible();expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);const box=await page.locator('.app-nav').boundingBox();expect(box!.y+box!.height).toBeLessThanOrEqual(width===320?640:844);if(locale==='en')expect(await page.locator('body').innerText()).not.toMatch(/[\u3400-\u9fff]/);expect(await page.locator('img').evaluateAll(imgs=>imgs.every(img=>img instanceof HTMLImageElement&&img.complete&&img.naturalWidth>0))).toBe(true)}
 await page.getByRole('button',{name:locale==='en'?'Chinese':'英文',exact:true}).click();await expect(page.locator('html')).toHaveAttribute('lang',locale==='en'?'zh-CN':'en');await page.reload();await expect(page.locator('html')).toHaveAttribute('lang',locale==='en'?'zh-CN':'en');expect(errors).toEqual([])
})
test('Today shows the full plan and starts with one explicit action',async({page})=>{const actions=await transport(page,createFixture());await page.goto('/today');await expect(page.getByText('Seated cable row',{exact:true})).toBeVisible();await expect(page.getByText('Lat pulldown',{exact:true})).toBeVisible();await expect(page.getByText('Dumbbell curl',{exact:true})).toBeVisible();await page.getByRole('button',{name:'Start workout',exact:true}).click();await expect(page).toHaveURL(/\/workout/);expect(actions.map(a=>a.kind)).toEqual(['start_workout']);await expect(page.locator('.app-nav')).toHaveCount(0);await page.getByRole('button',{name:'Complete exercise',exact:true}).click();await expect(page.getByText('1/3 exercises complete',{exact:true})).toBeVisible()})
test('Today applies the visible candidate and requests start together',async({page})=>{const state=createFixture();state.proposals=[proposal(state,'workout')];const actions=await transport(page,state);await page.goto('/today');await expect(page.locator('.today-gym')).toContainText('About 25 min');await page.getByRole('button',{name:'Apply changes and start',exact:true}).click();await expect(page).toHaveURL(/\/workout/);expect(actions).toHaveLength(1);expect(actions[0]).toMatchObject({kind:'apply_proposal',proposalId:'test-proposal',startAfterApply:true,source:'today'})})
test('Rest confirmation never requests a workout start',async({page})=>{const state=createFixture('low_recovery');state.proposals=[proposal(state,'schedule')];const actions=await transport(page,state);await page.goto('/today');await page.getByRole('button',{name:'Confirm rest and reschedule',exact:true}).click();await expect(page).toHaveURL(/\/today/);expect(actions[0]).toMatchObject({kind:'apply_proposal',startAfterApply:false});await expect(page.getByRole('button',{name:'Start workout',exact:true})).toHaveCount(0)})
test('A failed save keeps the candidate on Today and never enters the workout',async({page})=>{const state=createFixture();state.proposals=[proposal(state,'workout')];await transport(page,state);await page.route('**/api/actions',route=>route.fulfill({json:{status:'conflict',requestId:'test',errorCode:'VERSION_CONFLICT'}}));await page.goto('/today');await page.getByRole('button',{name:'Apply changes and start',exact:true}).click();await expect(page).toHaveURL(/\/today/);await expect(page.locator('.today-error')).toBeVisible()})

// New tab entries start at the top; browser Back/Forward restores each entry's own position.
test('switching tabs resets the content viewport before showing the new page',async({page})=>{
 // Use a short viewport: the normal phone overview now fits without scrolling.
 await page.setViewportSize({width:390,height:560});await transport(page,createFixture());await page.goto('/today');await expect(page.getByRole('button',{name:'Start workout',exact:true})).toBeVisible()
 const viewport=page.locator('.app-main')
 async function expectOffset(offset:number){
  expect(await viewport.evaluate(el=>el.scrollTop)).toBe(offset)
  // Observe consecutive paint boundaries so a later router restoration cannot hide behind the first reset.
  const offsets=await viewport.evaluate(async el=>{const result:number[]=[];for(let frame=0;frame<3;frame++){await new Promise<void>(resolve=>requestAnimationFrame(()=>resolve()));result.push(el.scrollTop)}return result})
  expect(offsets).toEqual([offset,offset,offset])
 }
 await viewport.evaluate(el=>{el.scrollTop=500});expect(await viewport.evaluate(el=>el.scrollTop)).toBeGreaterThan(0)
 await page.getByRole('link',{name:'Profile',exact:true}).click();await expect(page.getByRole('heading',{name:'Profile',exact:true})).toBeVisible();await expectOffset(0)
 await page.getByRole('link',{name:'Today',exact:true}).click();await expect(page.locator('.today-page')).toBeVisible();await expectOffset(0)
 // Keyboard navigation and ordinary composer focus must remain usable without moving the outer viewport.
 const agentLink=page.getByRole('link',{name:'Agent',exact:true});await agentLink.focus();await expect(agentLink).toBeFocused();await page.keyboard.press('Enter');await expect(page.locator('.agent-page')).toBeVisible();await expectOffset(0)
 const composer=page.locator('.agent-page textarea');await composer.focus();await expect(composer).toBeFocused();await expectOffset(0)
 const trendsLink=page.getByRole('link',{name:'Trends',exact:true});await trendsLink.focus();await page.keyboard.press('Enter');await expect(page.locator('.trends-page')).toBeVisible();await expectOffset(0)
 await page.getByRole('link',{name:'Today',exact:true}).click();await expect(page.locator('.today-page')).toBeVisible();await expectOffset(0)
 await viewport.evaluate(el=>{el.scrollTop=500});const todayOffset=await viewport.evaluate(el=>el.scrollTop);expect(todayOffset).toBeGreaterThan(0)
 await page.getByRole('link',{name:'Profile',exact:true}).click();await expect(page.locator('.profile-page')).toBeVisible();await expectOffset(0)
 await viewport.evaluate(el=>{el.scrollTop=100});const profileOffset=await viewport.evaluate(el=>el.scrollTop);expect(profileOffset).toBeGreaterThan(0)
 await page.goBack();await expect(page.locator('.today-page')).toBeVisible();await expectOffset(todayOffset)
 await page.goForward();await expect(page.locator('.profile-page')).toBeVisible();await expectOffset(profileOffset)
})
