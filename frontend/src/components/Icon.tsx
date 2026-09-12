import {createElement} from 'react'
import {ImagePlus,ChevronDown,ChevronLeft,ChevronRight,Dumbbell,Undo2,Pencil,Square,RotateCcw,LoaderCircle,Clock3,ArrowLeft,Minus} from 'lucide-react'
import art from './icon-art.json'
interface ArtNode {tag:string;attrs:Record<string,string>;children:ArtNode[]}
const nodes=art as unknown as Record<string,ArtNode[]>
function draw(node:ArtNode,key:number):React.ReactNode{return createElement(node.tag,{...node.attrs,key},...node.children.map(draw))}
const aliases:Record<string,string>={'calendar-days':'calendar','settings-2':'settings',droplets:'water',house:'today',home:'today','message-circle':'agent','chart-no-axes-combined':'trends','user-round':'profile',moon:'moon','moon-star':'moon',x:'close',sparkles:'sparkle',utensils:'meal','check-check':'check',bell:'notification',droplet:'water','heart-pulse':'heart'}
const fallback={ 'image-plus':ImagePlus, 'chevron-down':ChevronDown,'chevron-left':ChevronLeft,'chevron-right':ChevronRight,dumbbell:Dumbbell,'undo-2':Undo2,undo:Undo2,pencil:Pencil,square:Square,'rotate-ccw':RotateCcw,loader:LoaderCircle,clock:Clock3,'arrow-left':ArrowLeft,minus:Minus}
export function Icon({name,size=20,className=''}:{name:string;size?:number;className?:string}){
 const asset=aliases[name]||name
 if(nodes[asset])return <svg className={`wellio-icon ${className}`} width={size} height={size} viewBox="0 0 32 32" aria-hidden="true" focusable="false">{nodes[asset].map(draw)}</svg>
 if(name.startsWith('arrow-')){const rotation:Record<string,number>={'arrow-up-right':0,'arrow-right':45,'arrow-down':135,'arrow-left':225,'arrow-up':-45};return <svg className={`wellio-icon ${className}`} style={{transform:`rotate(${rotation[name]||0}deg)`}} width={size} height={size} viewBox="0 0 32 32" aria-hidden="true">{nodes.arrow.map(draw)}</svg>}
 const Component=fallback[name as keyof typeof fallback]||Dumbbell
 return <Component size={size} strokeWidth={1.8} className={`wellio-icon ${className}`} aria-hidden="true"/>
}
export function NavArtwork({name,label,selected}:{name:string;label:string;selected:boolean}){
 return <svg className="nav-art" viewBox="0 0 90 76" aria-hidden="true" focusable="false">
  <g><rect x="5" y="10" width="80" height="63" rx="21" fill="#2B3A2B" opacity=".09"/><rect x="3" y="7" width="84" height="64" rx="22" fill={selected?'#A2B77A':'#D2D8C3'} stroke={selected?'#526942':'#BAC3AA'} strokeWidth="1.5"/></g>
  <g className="nav-face"><rect x="3" y="2" width="84" height="64" rx="22" fill={selected?'#F5FAD7':'#F8F8EF'} stroke={selected?'#2B3A2B':'#D1D8C4'} strokeWidth={selected?'1.8':'1.3'}/><path d="M 17 10 Q 21 6 29 6 H 63 Q 71 6 76 11" fill="none" stroke="white" strokeWidth="2.5" strokeLinecap="round"/>
   <g transform="translate(30 7) scale(.9375)">{(nodes[name]||nodes.today).map((node,index)=>draw(selected&&node.tag==='g'?{...node,attrs:{...node.attrs,fill:'#CFE3A6'}}:node,index))}</g>
   <text x="45" y="57" fontSize="14.5" fontWeight={selected?700:500} fill={selected?'#2B3A2B':'#5C6852'} textAnchor="middle">{label}</text>
  </g>
 </svg>
}
