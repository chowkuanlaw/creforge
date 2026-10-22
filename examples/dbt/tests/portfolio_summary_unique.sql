select as_of_month, product_type
from {{ ref('portfolio_summary') }}
group by as_of_month, product_type
having count(*) <> 1
