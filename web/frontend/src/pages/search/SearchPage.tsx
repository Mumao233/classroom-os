import { useEffect, useRef } from 'react';
import { SearchForm, type SearchProps } from '../../features/search/SearchForm';
import styles from './SearchPage.module.css';

export function SearchPage(props: SearchProps) {
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);
  return <main id="main-content" className={styles.page}>
    <h1 ref={heading} tabIndex={-1}>搜索</h1>
    <SearchForm {...props} />
  </main>;
}
