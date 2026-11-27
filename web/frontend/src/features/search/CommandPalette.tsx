import { useRef } from 'react';
import * as Modal from '@radix-ui/react-dialog';
import { Button, IconButton } from '../../ui';
import { SearchForm, type SearchProps } from './SearchForm';
import styles from './search.module.css';

type PaletteProps = SearchProps & {
  open: boolean;
  onClose: () => void;
  /**
   * While a capture is running the modal palette makes the rest of the page inert, so it carries
   * the stop action itself: a capture can always be stopped (Aurora capture rule).
   */
  onStopCapture?: () => void;
};

export function CommandPalette({ open, onClose, onStopCapture, ...props }: PaletteProps) {
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  return <Modal.Root open={open} onOpenChange={value => { if (!value) onClose(); }}>
    <Modal.Portal>
      <Modal.Overlay className={styles.overlay} />
      <Modal.Content className={styles.palette} onOpenAutoFocus={event => {
        trigger.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
        if (input.current) { event.preventDefault(); input.current.focus(); }
      }} onCloseAutoFocus={event => {
        if (trigger.current?.isConnected) { event.preventDefault(); trigger.current.focus(); }
      }}>
        <header className={styles.heading}>
          <Modal.Title>搜索</Modal.Title>
          <Modal.Close asChild><IconButton aria-label="关闭搜索">×</IconButton></Modal.Close>
        </header>
        <Modal.Description>搜索课程、课堂和学习资料。</Modal.Description>
        {onStopCapture && <Button variant="secondary" onClick={() => { onClose(); onStopCapture(); }}>停止采集</Button>}
        <SearchForm {...props} inputRef={input} />
      </Modal.Content>
    </Modal.Portal>
  </Modal.Root>;
}
