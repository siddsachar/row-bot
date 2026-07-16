import React from 'react';
import {useState} from 'react';

type ScreenshotProps = {
  id: string;
  alt: string;
  caption?: string;
};

export default function Screenshot({id, alt, caption}: ScreenshotProps): JSX.Element {
  const src = `/img/screenshots/real-ui/${id}.png`;
  const isMobile = id.startsWith('mobile-');
  const [missing, setMissing] = useState(false);
  if (missing) {
    if (process.env.NODE_ENV !== 'production') {
      return (
        <figure className="rowBotScreenshot rowBotScreenshotMissing">
          <figcaption>Missing screenshot: {id}</figcaption>
        </figure>
      );
    }
    return <></>;
  }
  return (
    <figure className={`rowBotScreenshot${isMobile ? ' rowBotScreenshotMobile' : ''}`}>
      <img
        src={src}
        alt={alt}
        loading="lazy"
        width={isMobile ? 390 : undefined}
        height={isMobile ? 844 : undefined}
        onError={() => setMissing(true)}
      />
      {caption ? <figcaption>{caption}</figcaption> : null}
    </figure>
  );
}
