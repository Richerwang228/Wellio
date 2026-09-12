import {useEffect,useLayoutEffect,useRef,useState,type ReactNode} from 'react'
import {Link,useRouterState,useNavigate} from '@tanstack/react-router'
import {Capacitor} from '@capacitor/core'
import {Keyboard} from '@capacitor/keyboard'
import {useI18n} from '../lib/i18n'
import {useWellio} from '../lib/wellio-context'
import {errorText} from '../lib/errors'
import {NavArtwork} from './Icon'
import {DemoPresenter} from './DemoPresenter'
import {isDemoMode} from '../lib/runtime-mode'
export function AppShell({children}:{children:ReactNode}){
 const {t,locale}=useI18n(),{snapshot,loading,error,refresh}=useWellio()
 const navigate=useNavigate(),previousEpoch=useRef<number|null>(null)
 useEffect(()=>{if(snapshot){if(!isDemoMode()&&previousEpoch.current!==null&&previousEpoch.current!==snapshot.resetEpoch)void navigate({to:'/agent'});previousEpoch.current=snapshot.resetEpoch}},[snapshot?.resetEpoch,navigate])
 const path=useRouterState({select:s=>s.location.pathname});const [keyboard,setKeyboard]=useState(false)
 const mainRef=useRef<HTMLElement>(null)
 useLayoutEffect(()=>{if(mainRef.current)mainRef.current.scrollTop=0},[path])
 useEffect(()=>{
  // Native resize changes innerHeight too, so viewport ratios cannot detect the keyboard there.
  if(Capacitor.isNativePlatform()){
   const listeners=[Keyboard.addListener('keyboardWillShow',()=>setKeyboard(true)),Keyboard.addListener('keyboardWillHide',()=>setKeyboard(false))]
   return()=>{for(const listener of listeners)void listener.then(handle=>handle.remove())}
  }
  const viewport=window.visualViewport;if(!viewport)return
  const resize=()=>setKeyboard(viewport.height<window.innerHeight*.76)
  viewport.addEventListener('resize',resize)
  return()=>viewport.removeEventListener('resize',resize)
 },[])
 const full=path.startsWith('/workout')
 const links=[{to:'/today',icon:'today',label:t('Today','今日')},{to:'/agent',icon:'agent',label:t('Agent','助手')},{to:'/trends',icon:'trends',label:t('Trends','趋势')},{to:'/profile',icon:'profile',label:t('Profile','我的')}] as const
 return <div className={isDemoMode()?'demo-stage':undefined}><div className={`app-shell ${full?'is-workout':''} ${keyboard?'keyboard-open':''}`}>
  <a className="skip-link" href="#main-content">{t('Skip to content','跳至内容')}</a>
  <main ref={mainRef} id="main-content" className="app-main" tabIndex={-1}>
   {loading&&!snapshot?<div className="app-loading" role="status"><img src="/assets/wellio-logo.png" alt="Wellio" width="80" height="80"/><h1>Wellio</h1><p>{t('Getting your day ready…','正在准备今日数据…')}</p></div>:!snapshot?<div className="app-loading" role="alert"><h1>Wellio</h1><p>{errorText(error||'NETWORK_ERROR',locale)}</p><button className="primary-button" onClick={()=>void refresh()}>{t('Try again','重试')}</button></div>:children}
  </main>
  {!full&&!keyboard&&<nav className="app-nav" aria-label={t('Main navigation','主导航')}>{links.map(item=><Link key={item.to} to={item.to} search={true} aria-label={item.label} aria-current={path===item.to?'page':undefined} className="nav-key"><NavArtwork name={item.icon} label={item.label} selected={path===item.to}/></Link>)}</nav>}
 </div>{isDemoMode()&&<DemoPresenter/>}</div>
}
