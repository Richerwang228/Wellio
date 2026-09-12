import { useEffect, useRef, useState } from 'react'
import './mascot.css'

type Pose = 'welcome' | 'thinking' | 'sleep' | 'celebrate' | 'recover' | 'pace' | 'ready' | 'energized' | 'row' | 'pulldown' | 'lateral' | 'squat'
const readinessPoses: Pose[] = ['recover', 'pace', 'ready', 'energized']
const exercises: Pose[] = ['row', 'pulldown', 'lateral', 'squat']

export function Mascot({ pose = 'welcome', className = '', alt = 'Wellio' }: { pose?: Pose; className?: string; alt?: string }) {
  const ref = useRef<HTMLImageElement>(null)
  const [active, setActive] = useState(false)
  const [celebrationFinished, setCelebrationFinished] = useState(false)
  const readiness = readinessPoses.includes(pose)
  const exercise = exercises.includes(pose)

  useEffect(() => {
    const reduced = matchMedia('(prefers-reduced-motion: reduce)')
    let visible = false
    const sync = () => setActive(visible && !document.hidden && !reduced.matches)
    const observer = new IntersectionObserver(entries => { visible = entries[0]?.isIntersecting ?? false; sync() })
    if (ref.current) observer.observe(ref.current)
    document.addEventListener('visibilitychange', sync)
    reduced.addEventListener('change', sync)
    return () => { observer.disconnect(); document.removeEventListener('visibilitychange', sync); reduced.removeEventListener('change', sync) }
  }, [])

  useEffect(() => { setCelebrationFinished(false) }, [pose])
  // Celebration is a short reward. Exercise demonstrations keep looping.
  useEffect(() => {
    if (!active || pose !== 'celebrate' || celebrationFinished) return
    const timer = setTimeout(() => setCelebrationFinished(true), 3000)
    return () => clearTimeout(timer)
  }, [active, pose, celebrationFinished])

  const animatedAsset = active && (exercise || (pose === 'celebrate' && !celebrationFinished))
  const src = readiness ? `/assets/readiness/${pose}.webp` : `/assets/wellio/${pose}/${animatedAsset ? 'animation' : 'poster'}.webp`
  // Idle and thinking use a single clean silhouette, animated continuously by CSS.
  // This avoids sprite alignment jumps and does not imply these files contain frames.
  const motion = active && !exercise && !animatedAsset ? pose === 'thinking' ? 'mascot-thinking' : pose === 'welcome' ? 'mascot-idle' : readiness ? 'mascot-breathe' : '' : ''
  return <img ref={ref} className={`wellio-mascot ${motion} ${className}`} data-pose={pose} src={src} alt={alt} width="256" height="256" draggable="false" />
}
