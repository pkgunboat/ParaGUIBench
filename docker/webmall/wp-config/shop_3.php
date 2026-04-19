<?php
/**
 * WordPress configuration for WebMall Shop 3.
 *
 * This file is injected into the WordPress volume during setup_webmall.sh.
 * SHOP3_PORT_PLACEHOLDER, DB_PASSWORD_PLACEHOLDER, and SALT_*_PLACEHOLDER
 * are replaced with actual values before injection.
 */

define( 'DB_NAME', 'bitnami_wordpress' );
define( 'DB_USER', 'bn_wordpress' );
define( 'DB_PASSWORD', 'DB_PASSWORD_PLACEHOLDER' );
define( 'DB_HOST', 'mariadb-shop3:3306' );
define( 'DB_CHARSET', 'utf8' );
define( 'DB_COLLATE', '' );

define( 'AUTH_KEY',         'AUTH_KEY_PLACEHOLDER' );
define( 'SECURE_AUTH_KEY',  'SECURE_AUTH_KEY_PLACEHOLDER' );
define( 'LOGGED_IN_KEY',    'LOGGED_IN_KEY_PLACEHOLDER' );
define( 'NONCE_KEY',        'NONCE_KEY_PLACEHOLDER' );
define( 'AUTH_SALT',        'AUTH_SALT_PLACEHOLDER' );
define( 'SECURE_AUTH_SALT', 'SECURE_AUTH_SALT_PLACEHOLDER' );
define( 'LOGGED_IN_SALT',   'LOGGED_IN_SALT_PLACEHOLDER' );
define( 'NONCE_SALT',       'NONCE_SALT_PLACEHOLDER' );

$table_prefix = 'wp_';
define( 'WP_DEBUG', false );

define( 'WP_HOME', 'http://localhost:SHOP3_PORT_PLACEHOLDER' );
define( 'WP_SITEURL', 'http://localhost:SHOP3_PORT_PLACEHOLDER' );
define( 'FS_METHOD', 'direct' );

if ( ! defined( 'ABSPATH' ) ) {
	define( 'ABSPATH', __DIR__ . '/' );
}

require_once ABSPATH . 'wp-settings.php';
