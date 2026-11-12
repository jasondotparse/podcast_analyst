import Markdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

// Assistant text only. Raw HTML is skipped and images are dropped so a reply cannot load external content;
// react-markdown's default URL transform strips unsafe links such as javascript:.
const components = {
  a: ({ node, ...props }) => <a {...props} target="_blank" rel="noopener noreferrer" />,
}

export default function MarkdownMessage({ text }) {
  return (
    <div className="markdown">
      <Markdown remarkPlugins={[remarkGfm]} skipHtml disallowedElements={['img']} components={components}>
        {text}
      </Markdown>
    </div>
  )
}
