import React from 'react';

type ScreenshotProps = {
  id: string;
  alt: string;
  caption?: string;
};

export default function Screenshot({id, alt, caption}: ScreenshotProps): JSX.Element {
  const src = `/img/screenshots/generated/${id}.png`;
  return (
    <figure className="rowBotScreenshot">
      <img src={src} alt={alt} loading="lazy" />
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  );
}
