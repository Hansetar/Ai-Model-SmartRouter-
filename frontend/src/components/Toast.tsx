import { useState, useCallback, createContext, useContext, ReactNode } from 'react';

interface Toast {
  id: number;
  message: string;
  type: 'success' | 'error' | 'info' | 'warning';
  detail?: string;
}

interface ToastContextType {
  addToast: (message: string, type?: Toast['type'], detail?: string) => void;
}

const ToastContext = createContext<ToastContextType>({ addToast: () => {} });

export function useToast() {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  let nextId = 0;

  const addToast = useCallback((message: string, type: Toast['type'] = 'info', detail?: string) => {
    const id = Date.now() + nextId++;
    setToasts((prev) => [...prev, { id, message, type, detail }]);
    // Auto-remove after 4 seconds
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, 4000);
  }, []);

  const removeToast = (id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  };

  const colorMap: Record<Toast['type'], string> = {
    success: 'bg-green-500',
    error: 'bg-red-500',
    warning: 'bg-yellow-500',
    info: 'bg-blue-500',
  };

  const iconMap: Record<Toast['type'], string> = {
    success: '✓',
    error: '✕',
    warning: '⚠',
    info: 'ℹ',
  };

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      {/* Toast container */}
      <div className="fixed top-4 right-4 z-[100] space-y-2 max-w-sm">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`${colorMap[toast.type]} text-white px-4 py-3 rounded-lg shadow-lg flex items-start gap-2 animate-slide-in`}
          >
            <span className="font-bold text-sm">{iconMap[toast.type]}</span>
            <div className="flex-1">
              <div className="text-sm font-medium">{toast.message}</div>
              {toast.detail && (
                <div className="text-xs opacity-80 mt-1">{toast.detail}</div>
              )}
            </div>
            <button
              onClick={() => removeToast(toast.id)}
              className="text-white/70 hover:text-white text-sm"
            >
              ✕
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
