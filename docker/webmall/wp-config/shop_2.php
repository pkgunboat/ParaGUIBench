<?php
/**
 * WordPress configuration for WebMall Shop 2.
 */
define( 'DB_NAME', 'bitnami_wordpress' );
define( 'DB_USER', 'bn_wordpress' );
define( 'DB_PASSWORD', 'wordpress_db_password' );
define( 'DB_HOST', 'mariadb-shop2:3306' );
define( 'DB_CHARSET', 'utf8' );
define( 'DB_COLLATE', '' );

define( 'AUTH_KEY',         '. ~R8XIG]?G}e:{mH30n#L{{*g6cXmy:n*-V?!zHrf%(nL8AJ8_k?S,..BFc*%yD' );
define( 'SECURE_AUTH_KEY',  'IvLi3K4T`vm#%[6`A:z6MLpu1$o~$jG5gtZ^!sy(Q{>&Q*F38CA7>Nt`Y*jeNuc|' );
define( 'LOGGED_IN_KEY',    '?1}n} BhU@rO_=#J%?bTq;;>[)EOim}nV=~l[M5em#`<]h*%wCTm9`hbj4dR47o)' );
define( 'NONCE_KEY',        '6lai1^zNnF*V1i8b#!Rvw4UayTwlu)s-~2U]Ko{TT9P5>tv!gzOR,y):U)~C&wO~' );
define( 'AUTH_SALT',        '^c%sX|A[nCeN[g}cUqD7&dv)5o/g+J9C8u!bhUTXR,6AW:)c)JG!Vd 4!(G.!uqG' );
define( 'SECURE_AUTH_SALT', ':LRP[Y~G}>?w`L~*oq/5L0mw:L+_1AH[Ary5j0F+D8<xpgC#^8c25{,4i:iE*@{X' );
define( 'LOGGED_IN_SALT',   'ej/X sJXJ]w{88yd)4q~O4rlUV#(QbL%{A2ac3NSr1HO@pl?PW2EwDU]/34?JmmA' );
define( 'NONCE_SALT',       'l!x{9/|adgf0{wS`Ras_`DAUROx1vWjy3Y{*{ngki3v6t/w9|!1U!S/RwtBFr[K5' );

$table_prefix = 'wp_';
define( 'WP_DEBUG', false );

define( 'WP_HOME', 'http://localhost:SHOP2_PORT_PLACEHOLDER' );
define( 'WP_SITEURL', 'http://localhost:SHOP2_PORT_PLACEHOLDER' );
define( 'FS_METHOD', 'direct' );

if ( ! defined( 'ABSPATH' ) ) {
	define( 'ABSPATH', __DIR__ . '/' );
}

require_once ABSPATH . 'wp-settings.php';
