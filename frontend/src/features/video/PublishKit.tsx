import { useEffect, useState } from 'react';
import { Captions, Copy, Download, Send } from 'lucide-react';
import type { VideoProjectView, VideoPublishFile } from '../../api/types';
import { useRuntime } from '../../runtime';
import { IconButton, Select } from '../../ui/primitives';
import { acquire, release } from './media-urls';
import { PLATFORMS, type Platform, publishDescription } from './video-words';

/**
 * A made version's publish kit (C10): made by Row-Bot when the person names
 * where the video is going, here or in the chat (G9). It shows each
 * thumbnail, the post's title and description to copy, and the subtitles;
 * each platform's video is the version's own, or its shape under Other
 * shapes. Posting stays with the person's apps, which ask first.
 */
export function PublishKit({
  view,
  busy,
  onAsk,
  onSave,
  onCopy,
}: {
  view: VideoProjectView;
  busy: boolean;
  onAsk: (text: string) => void;
  onSave: (ref: string, name: string) => void;
  onCopy: (text: string, what: string) => void;
}) {
  const kit = view.publish;
  const [platform, setPlatform] = useState<Platform>(
    kit?.platforms[0] ?? 'youtube',
  );
  const where = PLATFORMS[platform];
  return (
    <section className="video-publish" aria-label="Ready to publish">
      <div className="video-publish-head">
        <h3>
          {kit
            ? `Ready to publish · ${kit.platforms.map((name) => PLATFORMS[name]).join(', ')}`
            : 'Ready to publish'}
        </h3>
        <div className="video-publish-ask">
          <Select
            aria-label="Where it's going"
            value={platform}
            disabled={busy}
            onChange={(event) => setPlatform(event.target.value as Platform)}
          >
            {Object.entries(PLATFORMS).map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </Select>
          <IconButton
            size="sm"
            label={`Get it ready for ${where}`}
            disabled={busy}
            onClick={() =>
              onAsk(
                `Get version ${view.number} of the video "${view.title}" (${view.project_id}) ready to publish on ${where}: its thumbnail, title, description and subtitles.`,
              )
            }
          >
            <Send size={16} aria-hidden />
          </IconButton>
        </div>
      </div>
      {kit && (
        <>
          <ul className="video-publish-thumbnails" aria-label="Thumbnails">
            {kit.thumbnails.map((file) => (
              <Thumbnail key={file.ref} file={file} onSave={onSave} />
            ))}
          </ul>
          <dl className="video-publish-words">
            <div>
              <dt>Title</dt>
              <dd>{kit.title}</dd>
              <IconButton
                size="sm"
                label="Copy the title"
                onClick={() => onCopy(kit.title, 'Title')}
              >
                <Copy size={16} aria-hidden />
              </IconButton>
            </div>
            {publishDescription(kit) && (
              <div>
                <dt>
                  {kit.chapters?.length
                    ? 'Description and chapters'
                    : 'Description'}
                </dt>
                <dd className="video-publish-description">
                  {publishDescription(kit)}
                </dd>
                <IconButton
                  size="sm"
                  label="Copy the description"
                  onClick={() => onCopy(publishDescription(kit), 'Description')}
                >
                  <Copy size={16} aria-hidden />
                </IconButton>
              </div>
            )}
            <div>
              <dt>Subtitles</dt>
              <dd>
                {kit.subtitles.length
                  ? 'SRT for YouTube and LinkedIn, WebVTT for the web'
                  : 'None: nothing in it is said'}
              </dd>
              {kit.subtitles.length > 0 && (
                <span className="video-publish-files">
                  {kit.subtitles.map((file) => (
                    <IconButton
                      key={file.ref}
                      size="sm"
                      label={`Download subtitles (${(file.format ?? '').toUpperCase()})`}
                      onClick={() => onSave(file.ref, file.name)}
                    >
                      <Captions size={16} aria-hidden />
                    </IconButton>
                  ))}
                </span>
              )}
            </div>
          </dl>
        </>
      )}
    </section>
  );
}

function Thumbnail({
  file,
  onSave,
}: {
  file: VideoPublishFile;
  onSave: (ref: string, name: string) => void;
}) {
  const { controller } = useRuntime();
  const [url, setUrl] = useState('');
  useEffect(() => {
    let live = true;
    void acquire(file.ref, (signal) =>
      controller.download(file.ref, signal),
    ).then((next) => {
      if (live) setUrl(next);
    });
    return () => {
      live = false;
      release(file.ref);
    };
  }, [controller, file.ref]);
  return (
    <li className="video-publish-thumbnail" data-shape={file.shape}>
      {url ? (
        <img src={url} alt={`Thumbnail, ${file.shape}`} />
      ) : (
        <span className="video-publish-placeholder" aria-hidden />
      )}
      <span className="video-publish-thumbnail-foot">
        <small>{file.shape}</small>
        <IconButton
          size="sm"
          label={`Download the ${file.shape} thumbnail`}
          onClick={() => onSave(file.ref, file.name)}
        >
          <Download size={16} aria-hidden />
        </IconButton>
      </span>
    </li>
  );
}
