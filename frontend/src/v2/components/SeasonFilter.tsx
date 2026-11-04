import React, { useEffect, useState } from 'react';
import { Calendar } from 'lucide-react';
import { API_URL } from '../../config';

interface SeasonFilterProps {
    onSeasonChange: (season: string) => void;
    currentSeason: string;
}

export const SeasonFilter = ({ onSeasonChange, currentSeason }: SeasonFilterProps) => {
    const [seasons, setSeasons] = useState<string[]>(['All Seasons']);

    useEffect(() => {
        // Real seasons from Home / Sales overview (not legacy /dashboard/seasons stub)
        fetch(`${API_URL}/executive-overview/breakdowns?year=2027`)
            .then(res => res.json())
            .then(data => {
                const list = Array.isArray(data?.seasons) ? data.seasons.filter(Boolean) : [];
                setSeasons(list.length ? ['All Seasons', ...list] : ['All Seasons']);
            })
            .catch(err => console.error("Failed to fetch seasons:", err));
    }, []);

    return (
        <div className="relative group">
            <Calendar className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-emerald-500 transition-colors group-hover:text-emerald-400" />
            <select
                value={currentSeason}
                onChange={(e) => onSeasonChange(e.target.value)}
                className="pl-10 pr-8 py-1.5 bg-slate-50 dark:bg-slate-800/50 border border-slate-200 dark:border-slate-700 rounded-lg text-sm font-semibold text-slate-700 dark:text-slate-200 focus:outline-none focus:ring-2 focus:ring-emerald-500/20 transition-all appearance-none min-w-[160px] hover:border-slate-300 dark:hover:border-slate-600 cursor-pointer"
            >

                {seasons.map(season => (
                    <option key={season} value={season} className="bg-white dark:bg-slate-900">{season}</option>
                ))}
            </select>
            <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
                <svg className="w-4 h-4 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M19 9l-7 7-7-7" />
                </svg>
            </div>
        </div>
    );
};
