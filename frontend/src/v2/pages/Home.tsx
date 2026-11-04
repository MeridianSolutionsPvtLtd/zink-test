import React, { useState } from 'react';
import { Cloud, CloudUpload } from 'lucide-react';
import DashboardLayout from '../layouts/DashboardLayout';
import { FileUploadPanel } from '../components/FileUploadPanel';
import { BlobFetchPanel } from '../components/BlobFetchPanel';

export default function Home() {
  const [tab, setTab] = useState<'manual' | 'blob'>('blob');

  return (
    <DashboardLayout>
      <div className="flex min-h-[calc(100vh-8rem)] flex-col items-center px-4 py-8 lg:py-12">
        <div className="mb-8 flex w-full max-w-5xl flex-col items-center">
          <div className="inline-flex items-center gap-1 rounded-2xl border border-slate-200/90 bg-white p-1.5 shadow-lg shadow-slate-200/50 dark:border-slate-700 dark:bg-slate-900 dark:shadow-none">
            <button
              type="button"
              onClick={() => setTab('manual')}
              className={`inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-bold transition ${
                tab === 'manual'
                  ? 'bg-gradient-to-r from-emerald-500 to-teal-600 text-white shadow-md shadow-emerald-500/25'
                  : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200'
              }`}
            >
              <CloudUpload className="h-4 w-4" />
              Manual Upload
            </button>
            <button
              type="button"
              onClick={() => setTab('blob')}
              className={`inline-flex items-center gap-2 rounded-xl px-5 py-2.5 text-sm font-bold transition ${
                tab === 'blob'
                  ? 'bg-gradient-to-r from-indigo-500 to-sky-600 text-white shadow-md shadow-indigo-500/25'
                  : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700 dark:hover:bg-slate-800 dark:hover:text-slate-200'
              }`}
            >
              <Cloud className="h-4 w-4" />
              Upload from Blob
            </button>
          </div>
        </div>
        {tab === 'manual' ? <FileUploadPanel /> : <BlobFetchPanel />}
      </div>
    </DashboardLayout>
  );
}
