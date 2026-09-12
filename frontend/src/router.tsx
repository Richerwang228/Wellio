import {createRouter} from '@tanstack/react-router'
import {routeTree} from './routeTree.gen'
// New routes reset the shared content viewport; history entries still restore their own offset.
export function getRouter(){return createRouter({routeTree,scrollRestoration:true,scrollToTopSelectors:['#main-content'],defaultPreload:'intent'})}
declare module '@tanstack/react-router' {interface Register {router:ReturnType<typeof getRouter>}}
