import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'investigate' },
  {
    path: 'investigate',
    loadComponent: () => import('./investigator.component').then((m) => m.Investigator),
    title: 'BugZero — Investigate',
  },
  {
    path: 'history',
    loadComponent: () => import('./history.component').then((m) => m.History),
    title: 'BugZero — History',
  },
  { path: '**', redirectTo: 'investigate' },
];