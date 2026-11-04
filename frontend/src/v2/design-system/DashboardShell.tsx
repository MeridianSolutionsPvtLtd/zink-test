import React from 'react';

type Props = {
  children: React.ReactNode;
  className?: string;
  maxWidth?: boolean;
};

/** Standard dashboard page wrapper — consistent spacing & animation */
export function DashboardShell({ children, className = '', maxWidth = true }: Props) {
  const gap = /\bspace-y-/.test(className) ? '' : 'space-y-4';
  return (
    <div className={`animate-fade-in ${gap} pb-8 ${maxWidth ? 'mx-auto max-w-[1920px]' : ''} ${className}`.trim()}>
      {children}
    </div>
  );
}
