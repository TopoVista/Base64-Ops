import { Link } from 'react-router-dom'
import logo from '@/assets/logo.png'

const Logo = ({className, showText = true}: {className?: string, showText?: boolean}) => {
    return (
        <Link to="/" className={`flex items-center gap-2 font-medium ${className}`}>
           <div className="size-7"> <img src={logo} alt="" aria-hidden="true" className="size-full" /></div>
            {showText && <span className='font-semibold text-xl dark:text-white'>Base64 Ops</span>}
        </Link>
    )
}

export default Logo

