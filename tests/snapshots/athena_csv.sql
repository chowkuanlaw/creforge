
CREATE EXTERNAL TABLE IF NOT EXISTS subject (
    `subject_id` string,
    `birth_year` int,
    `region` string,
    `income_band` string,
    `risk_grade` string,
    `created_month` date
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://bucket/creforge/subject/'
TBLPROPERTIES ('skip.header.line.count'='1', 'serialization.null.format'='');

CREATE EXTERNAL TABLE IF NOT EXISTS inquiry (
    `inquiry_id` string,
    `subject_id` string,
    `inquiry_date` date,
    `lender_id` string,
    `product_type` string,
    `requested_amount` decimal(18,2),
    `outcome` string
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://bucket/creforge/inquiry/'
TBLPROPERTIES ('skip.header.line.count'='1', 'serialization.null.format'='');

CREATE EXTERNAL TABLE IF NOT EXISTS account (
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
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://bucket/creforge/account/'
TBLPROPERTIES ('skip.header.line.count'='1', 'serialization.null.format'='');

CREATE EXTERNAL TABLE IF NOT EXISTS account_month (
    `account_id` string,
    `as_of_month` date,
    `balance` decimal(18,2),
    `amount_due` decimal(18,2),
    `amount_paid` decimal(18,2),
    `dpd_bucket` string,
    `months_in_arrears` int,
    `status` string
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://bucket/creforge/account_month/'
TBLPROPERTIES ('skip.header.line.count'='1', 'serialization.null.format'='');

CREATE EXTERNAL TABLE IF NOT EXISTS account_party (
    `account_id` string,
    `subject_id` string,
    `role` string,
    `start_date` date
)
ROW FORMAT DELIMITED FIELDS TERMINATED BY ','
STORED AS TEXTFILE
LOCATION 's3://bucket/creforge/account_party/'
TBLPROPERTIES ('skip.header.line.count'='1', 'serialization.null.format'='');
