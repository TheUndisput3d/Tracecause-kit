import { Link, useParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import rehypeSanitize from 'rehype-sanitize'
import dataCard from '../../../../docs/DATA_CARD.md?raw'
import captureCard from '../../../../docs/CAPTURE_DATA_CARD.md?raw'
import modelCard from '../../../../docs/MODEL_CARD.md?raw'
export function Documentation() {
  const { card = '' } = useParams()
  const cards: Record<string,string> = { data:dataCard,capture:captureCard,model:modelCard }
  return <><div className="toolbar"><Link to="/about/data">Synthetic data card</Link><Link to="/about/capture">Captured data card</Link><Link to="/about/model">Model card</Link></div><article className="panel documentation">{cards[card] ? <ReactMarkdown skipHtml rehypePlugins={[rehypeSanitize]}>{cards[card]}</ReactMarkdown> : <h1>Documentation not found</h1>}</article></>
}
