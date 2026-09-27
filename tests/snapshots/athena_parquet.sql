
CREATE DATABASE IF NOT EXISTS creforge;

CREATE EXTERNAL TABLE IF NOT EXISTS creforge.subject (
    `subject_id` string,
    `birth_year` int,
    `region` string,
    `income_band` string,
    `risk_grade` string,
    `created_month` date
)
STORED AS PARQUET
LOCATION 's3://bucket/creforge/subject/';

CREATE EXTERNAL TABLE IF NOT EXISTS creforge.inquiry (
    `inquiry_id` string,
    `subject_id` string,
    `inquiry_date` date,
    `lender_id` string,
    `product_type` string,
    `requested_amount` decimal(18,2),
    `outcome` string
)
STORED AS PARQUET
LOCATION 's3://bucket/creforge/inquiry/';

CREATE EXTERNAL TABLE IF NOT EXISTS creforge.account (
    `account_id` string,
    `subject_id` string,
    `inquiry_id` string,
    `lender_id` string,
    `product_type` string,
    `open_date` date,
    `credit_limit` decimal(18,2),
    `principal` decimal(18,2),
    `tenor_months` int,
    `interest_rate` double,
    `secured` boolean,
    `close_date` date,
    `close_reason` string
)
STORED AS PARQUET
LOCATION 's3://bucket/creforge/account/';

CREATE EXTERNAL TABLE IF NOT EXISTS creforge.account_month (
    `account_id` string,
    `as_of_month` date,
    `balance` decimal(18,2),
    `amount_due` decimal(18,2),
    `amount_paid` decimal(18,2),
    `dpd_bucket` string,
    `months_in_arrears` int,
    `status` string
)
STORED AS PARQUET
LOCATION 's3://bucket/creforge/account_month/';

CREATE EXTERNAL TABLE IF NOT EXISTS creforge.account_party (
    `account_id` string,
    `subject_id` string,
    `role` string,
    `start_date` date
)
STORED AS PARQUET
LOCATION 's3://bucket/creforge/account_party/';
