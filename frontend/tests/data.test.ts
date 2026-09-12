import {describe,it,expect} from 'vitest'
import {createFixture} from '../src/lib/fixtures'
import {dailyTotals} from '../src/lib/format'
import {errorText} from '../src/lib/errors'
describe('Documented seed invariants',()=>{
 it('matches the nutrition ledger without counting future meals',()=>{expect(dailyTotals(createFixture().meals)).toEqual({kcal:1650,protein:90,carbs:210,fat:50})})
 it('keeps recovery and sleep on the same scenario',()=>{const normal=createFixture(),low=createFixture('low_recovery');expect(normal.readiness.score).toBe(82);expect(normal.sleep?.minutes).toBe(450);expect(low.readiness.score).toBe(42);expect(low.sleep?.minutes).toBe(240);expect(low.history).toEqual(normal.history);expect(low.profile.targets).toEqual(normal.profile.targets)})
 it('preserves history and workout facts',()=>{const s=createFixture();expect(s.history.weight.at(-1)?.kg).toBe(70.4);expect(s.history.training.reduce((a,b)=>a+b.minutes,0)).toBe(270);expect(s.history.training.filter(r=>r.minutes>0)).toHaveLength(7);expect(s.history.nutrition.reduce((a,n)=>a+n.expenditure-n.kcal,0)).toBe(2568);expect(s.workout?.status).toBe('planned');expect(s.workout?.exercises.every(e=>!e.completed)).toBe(true)})
 it('applies fractions per food item without changing the others',()=>{const s=createFixture();s.meals[0].items[0].consumedFraction=.5;expect(dailyTotals(s.meals)).toEqual({kcal:1350,protein:75,carbs:172.5,fat:40});expect(s.meals[1].items[0].consumedFraction).toBe(1)})
 it('localizes generic and specific errors',()=>{for(const code of ['PROVIDER_NOT_CONFIGURED','VERSION_CONFLICT','INVALID_INPUT','anything']){expect(errorText(code,'en')).not.toMatch(/[\u3400-\u9fff]/);expect(errorText(code,'zh-CN')).toMatch(/[\u3400-\u9fff]/)}})
})
