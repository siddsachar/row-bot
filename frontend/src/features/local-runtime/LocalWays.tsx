import { useState, type ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { Download, ExternalLink, Settings2 } from 'lucide-react';
import { Button } from '../../ui/primitives';

/** One way to run models on this computer, as an equal row. */
type Way = {
  id: 'ollama' | 'lmstudio' | 'vllm' | 'llama_cpp' | 'row_bot';
  name: string;
  line: string;
  /** Where to get it (opens in the browser). */
  site?: string;
};

export const LOCAL_WAYS: readonly Way[] = [
  {
    id: 'ollama',
    name: 'Ollama',
    line: 'An app that downloads and runs models; Row-Bot uses the ones you pull.',
    site: 'https://ollama.com/download',
  },
  {
    id: 'lmstudio',
    name: 'LM Studio',
    line: 'A desktop app to find and run models, with a built-in server.',
    site: 'https://lmstudio.ai',
  },
  {
    id: 'vllm',
    name: 'vLLM',
    line: 'A fast server for larger models on NVIDIA cards, usually on Linux.',
    site: 'https://docs.vllm.ai',
  },
  {
    id: 'llama_cpp',
    name: 'llama.cpp server (your own)',
    line: 'Run llama-server yourself and connect Row-Bot to it.',
    site: 'https://github.com/ggml-org/llama.cpp',
  },
  {
    id: 'row_bot',
    name: 'Download with Row-Bot (llama.cpp)',
    line: 'Row-Bot downloads llama.cpp and a model that fits, and runs it only while a chat needs it.',
  },
];

/**
 * Every way to run models on this computer, with equal weight: Ollama, LM
 * Studio, vLLM, a llama.cpp server of your own, and Row-Bot's own download.
 * None is primary. A row's Set up opens its details below it (Ollama's steps,
 * Row-Bot's download) or the server form with that runtime picked.
 */
export default function LocalWays({
  ollama,
  ollamaStatus,
  download,
  downloadStatus,
  from,
  open,
  onOpen,
}: {
  /** Ollama's own steps (install, start, pull), shown under its row. */
  ollama?: ReactNode;
  ollamaStatus?: string;
  /** Row-Bot's download, shown under its row. */
  download: ReactNode;
  downloadStatus?: string;
  /** Where the server form returns to ("setup"). */
  from?: string;
  /** The row whose details show (controlled), and its change. */
  open?: Way['id'] | null;
  onOpen?: (id: Way['id'] | null) => void;
}) {
  const [own, setOwn] = useState<Way['id'] | null>(null);
  const shown = open === undefined ? own : open;
  const toggle = (id: Way['id']) => {
    const next = shown === id ? null : id;
    if (onOpen) onOpen(next);
    else setOwn(next);
  };
  const formLink = (profile: string) =>
    `/settings/models?add=custom-endpoint&type=${profile}${from ? `&from=${from}` : ''}`;
  return (
    <ul className="local-ways" aria-label="Ways to run models on this computer">
      {LOCAL_WAYS.map((way) => {
        const status =
          way.id === 'ollama'
            ? ollamaStatus
            : way.id === 'row_bot'
              ? downloadStatus
              : undefined;
        const details =
          way.id === 'ollama' ? ollama : way.id === 'row_bot' ? download : null;
        const expanded = shown === way.id && details != null;
        return (
          <li key={way.id} className="local-way" data-way={way.id}>
            <div className="local-way-row">
              <span className="local-way-text">
                <strong>{way.name}</strong>
                <span>{way.line}</span>
                {status && <small>{status}</small>}
              </span>
              <span className="local-way-actions">
                {way.site && (
                  <a
                    className="button ghost small"
                    href={way.site}
                    target="_blank"
                    rel="noopener noreferrer"
                    aria-label={`Get ${way.name}`}
                  >
                    <ExternalLink size={14} aria-hidden /> Get it
                  </a>
                )}
                {details != null ? (
                  <Button
                    className="small"
                    aria-expanded={expanded}
                    aria-label={`Set up ${way.name}`}
                    onClick={() => toggle(way.id)}
                  >
                    {way.id === 'row_bot' ? (
                      <Download size={14} aria-hidden />
                    ) : (
                      <Settings2 size={14} aria-hidden />
                    )}{' '}
                    Set up
                  </Button>
                ) : (
                  <Link
                    className="button secondary small"
                    to={formLink(way.id)}
                    aria-label={`Set up ${way.name}`}
                  >
                    <Settings2 size={14} aria-hidden /> Set up
                  </Link>
                )}
              </span>
            </div>
            {expanded && <div className="local-way-details">{details}</div>}
          </li>
        );
      })}
    </ul>
  );
}
