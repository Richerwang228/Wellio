import {createContext, useContext, useEffect, useState, type ReactNode} from 'react'
import type {Locale, LocalizedText} from './contracts'
interface I18nValue {
 locale: Locale; setLocale: (value: Locale) => void; t: (en: string, zh: string) => string;
 text: (value: string | LocalizedText) => string;
 date: (value: string, options?: Intl.DateTimeFormatOptions) => string;
 duration: (minutes: number) => string;
}
const I18nContext=createContext<I18nValue|null>(null)
export function I18nProvider({children}:{children:ReactNode}) {
 const [locale,setCurrent]=useState<Locale>('en')
 useEffect(()=>{try {const saved=localStorage.getItem('wellio.locale.v1'); if(saved==='en'||saved==='zh-CN')setCurrent(saved)}catch{}},[])
 useEffect(()=>{document.documentElement.lang=locale;document.title=locale==='en'?'Wellio — A healthier day':'Wellio · 健康每一天'},[locale])
 const setLocale=(value:Locale)=>{setCurrent(value);try{localStorage.setItem('wellio.locale.v1',value)}catch{}}
 const t=(en:string,zh:string)=>locale==='en'?en:zh
 const value:I18nValue={locale,setLocale,t,text:v=>typeof v==='string'?v:v[locale],
  date:(v,options)=>new Intl.DateTimeFormat(locale,{timeZone:'Asia/Hong_Kong',month:'short',day:'numeric',...options}).format(new Date(v.length===10?v+'T12:00:00+08:00':v)),
  duration:m=>{const h=Math.floor(m/60),r=Math.round(m%60);return locale==='en'?[h?`${h} h`:'',r?`${r} min`:''].filter(Boolean).join(' ')||'0 min':[h?`${h} 小时`:'',r?`${r} 分钟`:''].filter(Boolean).join(' ')||'0 分钟'} }
 return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>
}
export function useI18n(){const c=useContext(I18nContext);if(!c)throw new Error('I18nProvider missing');return c}
export const bilingual=(en:string,zh:string):LocalizedText=>({en,'zh-CN':zh})
