/** Development-only visual adapter. Production requests always go to the server. */
import {createFixture} from './fixtures'
import type {WellioClient,Snapshot,ActionResult} from './contracts'
import {ApiError} from './api-client'
let state:Snapshot=createFixture()
const copy=()=>structuredClone(state)
export const previewClient:WellioClient={
 async getSnapshot(){return copy()},
 async action(request){
  const fail=(errorCode:string):ActionResult=>({requestId:request.requestId,status:'failed',errorCode})
  if(request.resetEpoch!==state.resetEpoch)return fail('SESSION_RESET')
  if(request.kind==='reset_demo'){const epoch=state.resetEpoch+1;state=createFixture(request.scenario);state.resetEpoch=epoch}
  else if(request.kind==='set_locale')state.locale=request.locale
  else if(request.kind==='start_workout'){if(!state.workout||state.plan.restDates.includes(state.dayKey))return fail('NOT_FOUND');state.workout.status='in_progress';state.workout.version++}
  else if(request.kind==='complete_exercise'||request.kind==='undo_exercise'){const ex=state.workout?.exercises.find(e=>e.id===request.exerciseId);if(!ex)return fail('NOT_FOUND');ex.completed=request.kind==='complete_exercise';state.workout!.version++}
  else if(request.kind==='finish_workout'){if(!state.workout)return fail('NOT_FOUND');state.workout.status='completed';state.workout.actualMinutes=request.actualMinutes;state.workout.version++}
  else if(request.kind==='dismiss_proposal'){const p=state.proposals.find(p=>p.id===request.proposalId);if(!p)return fail('NOT_FOUND');p.status='dismissed'}
  else return fail('PROVIDER_NOT_CONFIGURED')
  state.revision++;return {requestId:request.requestId,status:'succeeded',snapshot:copy()}
 },
 async chat(){throw new ApiError('PROVIDER_NOT_CONFIGURED')},
 async upload(){throw new ApiError('PREVIEW_ONLY')},
}
