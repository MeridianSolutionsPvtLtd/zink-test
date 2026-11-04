import React, { useState, useRef, useEffect } from 'react';
import { createPortal } from 'react-dom';
import { Info } from 'lucide-react';

interface InfoTooltipProps {
    title: string;
    description: string;
    formula?: string;
    source?: string;
}

export const InfoTooltip = ({ title, description, formula, source }: InfoTooltipProps) => {
    const [open, setOpen] = useState(false);
    const [coords, setCoords] = useState({ top: 0, left: 0 });
    const triggerRef = useRef<HTMLButtonElement>(null);

    useEffect(() => {
        const handler = (e: MouseEvent) => {
            if (triggerRef.current && !triggerRef.current.contains(e.target as Node)) {
                setOpen(false);
            }
        };

        // Handle scroll to update position or close
        const scrollHandler = () => {
            if (open) setOpen(false);
        };

        window.addEventListener('mousedown', handler);
        window.addEventListener('scroll', scrollHandler, true); // true for capture to catch all scrolls
        window.addEventListener('resize', scrollHandler);

        return () => {
            window.removeEventListener('mousedown', handler);
            window.removeEventListener('scroll', scrollHandler, true);
            window.removeEventListener('resize', scrollHandler);
        };
    }, [open]);

    const handleToggle = (e: React.MouseEvent) => {
        e.stopPropagation(); // Prevent row clicks etc
        if (!open) {
            const rect = triggerRef.current?.getBoundingClientRect();
            if (rect) {
                // Position above centered
                setCoords({
                    top: rect.top - 8, // 8px margin
                    left: rect.left + (rect.width / 2)
                });
            }
        }
        setOpen(o => !o);
    };

    return (
        <>
            <button
                ref={triggerRef}
                onClick={handleToggle}
                className="ml-1.5 text-slate-400 hover:text-slate-600 dark:hover:text-slate-200 transition-colors inline-flex items-center justify-center"
                title="More info"
                type="button"
            >
                <Info className="w-3.5 h-3.5" />
            </button>

            {open && createPortal(
                <div
                    className="fixed z-[9999] w-72 pointer-events-none"
                    style={{
                        top: coords.top,
                        left: coords.left,
                        transform: 'translate(-50%, -100%)'
                    }}
                >
                    <div className="bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-xl shadow-xl p-4 text-left relative pointer-events-auto">
                        {/* Arrow */}
                        <div className="absolute top-full left-1/2 -translate-x-1/2 w-0 h-0 border-l-8 border-r-8 border-t-8 border-l-transparent border-r-transparent border-t-slate-200 dark:border-t-slate-700" />
                        <div className="absolute top-full left-1/2 -translate-x-1/2 mt-[-1px] w-0 h-0 border-l-[7px] border-r-[7px] border-t-[7px] border-l-transparent border-r-transparent border-t-white dark:border-t-slate-800" />

                        <div className="font-semibold text-slate-900 dark:text-white text-sm mb-1">{title}</div>
                        <div className="text-xs text-slate-500 dark:text-slate-400 mb-3 leading-relaxed">{description}</div>

                        {formula && (
                            <div className="bg-slate-50 dark:bg-slate-900 rounded-lg p-2.5 mb-2">
                                <div className="text-xs font-medium text-slate-400 dark:text-slate-500 uppercase tracking-wider mb-1">Formula</div>
                                <code className="text-xs text-emerald-600 dark:text-emerald-400 font-mono leading-relaxed">{formula}</code>
                            </div>
                        )}
                    </div>
                </div>,
                document.body
            )}
        </>
    );
};

export default InfoTooltip;
