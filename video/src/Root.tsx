import React from 'react';
import {Composition} from 'remotion';
import {Video, FPS, totalFrames} from './Video';
export const RemotionRoot: React.FC = () => (
  <Composition id="Teleport" component={Video} durationInFrames={totalFrames} fps={FPS} width={1920} height={1080} />
);
