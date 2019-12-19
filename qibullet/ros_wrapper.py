#!/usr/bin/env python
# coding: utf-8

import os
import sys
import atexit
import pybullet
from qibullet.camera import Camera
from qibullet.pepper_virtual import PepperVirtual
from qibullet.base_controller import PepperBaseController
from threading import Thread

try:
    import rospy
    import roslib
    import roslaunch
    import tf2_ros
    from cv_bridge import CvBridge
    from sensor_msgs.msg import Image
    from sensor_msgs.msg import CameraInfo
    from sensor_msgs.msg import JointState
    from sensor_msgs.msg import LaserScan
    from std_msgs.msg import Header
    from std_msgs.msg import Empty
    from naoqi_bridge_msgs.msg import JointAnglesWithSpeed
    from naoqi_bridge_msgs.msg import PoseStampedWithSpeed
    from geometry_msgs.msg import TransformStamped
    from geometry_msgs.msg import Twist
    from nav_msgs.msg import Odometry
    MISSING_IMPORT = None

except ImportError as e:
    MISSING_IMPORT = str(e)

TOP_OPTICAL_FRAME = "CameraTop_optical_frame"
BOTTOM_OPTICAL_FRAME = "CameraBottom_optical_frame"
DEPTH_OPTICAL_FRAME = "CameraDepth_optical_frame"


class RosWrapper:
    """
    Virtual class defining the basis of a robot ROS wrapper
    """

    def __init__(self):
        """
        Constructor
        """
        if MISSING_IMPORT is not None:
            raise pybullet.error(MISSING_IMPORT)

        self.spin_thread = None
        self._wrapper_termination = False
        self.image_bridge = CvBridge()
        self.front_info_msg = dict()
        self.bottom_info_msg = dict()
        self.depth_info_msg = dict()
        self.roslauncher = None
        self.transform_broadcaster = tf2_ros.TransformBroadcaster()
        atexit.register(self.stopWrapper)

    def stopWrapper(self):
        """
        Stops the ROS wrapper
        """
        self._wrapper_termination = True

        try:
            assert self.spin_thread is not None
            assert isinstance(self.spin_thread, Thread)
            assert self.spin_thread.isAlive()
            self.spin_thread.join()

        except AssertionError:
            pass

        if self.roslauncher is not None:
            self.roslauncher.stop()
            print("stopping roslauncher")


class PepperRosWrapper(RosWrapper):
    """
    Class describing a ROS wrapper for the virtual model of Pepper, inheriting
    from the RosWrapperClass
    """

    def __init__(self):
        """
        Constructor
        """
        RosWrapper.__init__(self)

    def launchWrapper(self, virtual_pepper, ros_namespace, frequency=200):
        """
        Launches the ROS wrapper for the pepper_virtual instance

        Parameters:
            virtual_pepper - The instance of the simulated model
            ros_namespace - The ROS namespace to be added before the ROS topics
            advertized and subscribed
            frequency - The frequency of the ROS rate that will be used to pace
            the wrapper's main loop
        """
        if MISSING_IMPORT is not None:
            raise pybullet.error(MISSING_IMPORT)

        self.virtual_pepper = virtual_pepper
        self.ros_namespace = ros_namespace
        self.frequency = frequency

        rospy.init_node(
            "pybullet_pepper",
            anonymous=True,
            disable_signals=False)

        self.front_cam_pub = rospy.Publisher(
            self.ros_namespace + '/camera/front/image_raw',
            Image,
            queue_size=10)

        self.front_info_pub = rospy.Publisher(
            self.ros_namespace + '/camera/front/camera_info',
            CameraInfo,
            queue_size=10)

        self.bottom_cam_pub = rospy.Publisher(
            self.ros_namespace + '/camera/bottom/image_raw',
            Image,
            queue_size=10)

        self.bottom_info_pub = rospy.Publisher(
            self.ros_namespace + '/camera/bottom/camera_info',
            CameraInfo,
            queue_size=10)

        self.depth_cam_pub = rospy.Publisher(
            self.ros_namespace + '/camera/depth/image_raw',
            Image,
            queue_size=10)

        self.depth_info_pub = rospy.Publisher(
            self.ros_namespace + '/camera/depth/camera_info',
            CameraInfo,
            queue_size=10)

        self.laser_pub = rospy.Publisher(
            self.ros_namespace + "/laser",
            LaserScan,
            queue_size=10)

        self.joint_states_pub = rospy.Publisher(
            '/joint_states',
            JointState,
            queue_size=10)

        self.odom_pub = rospy.Publisher(
            'odom',
            Odometry,
            queue_size=10)

        rospy.Subscriber(
            '/joint_angles',
            JointAnglesWithSpeed,
            self._jointAnglesCallback)

        rospy.Subscriber(
            '/cmd_vel',
            Twist,
            self._velocityCallback)

        rospy.Subscriber(
            '/move_base_simple/goal',
            PoseStampedWithSpeed,
            self._moveToCallback)

        rospy.Subscriber(
            '/move_base_simple/cancel',
            Empty,
            self._killMoveCallback)

        try:
            package_path = roslib.packages.get_pkg_dir("naoqi_driver")
            # path = os.path.dirname(os.path.abspath(__file__)) + "/"

            with open(package_path + "/share/urdf/pepper.urdf", 'r') as file:
                robot_description = file.read()

            # robot_description = robot_description.replace(
            #     "meshes/",
            #     "package://pepper_meshes/meshes/1.0/")

            rospy.set_param("/robot_description", robot_description)

            robot_state_publisher = roslaunch.core.Node(
                "robot_state_publisher",
                "robot_state_publisher")

            self.roslauncher = roslaunch.scriptapi.ROSLaunch()
            self.roslauncher.start()
            self.roslauncher.launch(robot_state_publisher)

            # Launch the wrapper's main loop
            self._wrapper_termination = False
            self.spin_thread = Thread(target=self._spin)
            self.spin_thread.start()

        except IOError as e:
            print("Could not retrieve robot descrition: " + str(e))
            return

    def _updateLasers(self):
        """
        INTERNAL METHOD, updates the laser values in the ROS framework
        """
        if not self.virtual_pepper.laser_manager.isActive():
            return

        scan = LaserScan()
        scan.header.stamp = rospy.get_rostime()
        scan.header.frame_id = "base_footprint"
        # -120 degres, 120 degres
        scan.angle_min = -2.0944
        scan.angle_max = 2.0944

        # 240 degres FoV, 61 points (blind zones inc)
        scan.angle_increment = (2 * 2.0944) / (15.0 + 15.0 + 15.0 + 8.0 + 8.0)

        # Detection ranges for the lasers in meters, 0.1 to 3.0 meters
        scan.range_min = 0.1
        scan.range_max = 3.0

        # Fill the lasers information
        right_scan = self.virtual_pepper.getRightLaserValue()
        front_scan = self.virtual_pepper.getFrontLaserValue()
        left_scan = self.virtual_pepper.getLeftLaserValue()

        if isinstance(right_scan, list):
            scan.ranges.extend(list(reversed(right_scan)))
            scan.ranges.extend([-1]*8)
        if isinstance(front_scan, list):
            scan.ranges.extend(list(reversed(front_scan)))
            scan.ranges.extend([-1]*8)
        if isinstance(left_scan, list):
            scan.ranges.extend(list(reversed(left_scan)))

        self.laser_pub.publish(scan)

    def _broadcastOdom(self):
        """
        INTERNAL METHOD, updates and broadcasts the odometry by broadcasting
        based on the robot's base tranform
        """
        # Send Transform odom
        x, y, theta = self.virtual_pepper.getPosition()
        odom_trans = TransformStamped()
        odom_trans.header.frame_id = "odom"
        odom_trans.child_frame_id = "base_link"
        odom_trans.header.stamp = rospy.get_rostime()
        odom_trans.transform.translation.x = x
        odom_trans.transform.translation.y = y
        odom_trans.transform.translation.z = 0
        quaternion = pybullet.getQuaternionFromEuler([0, 0, theta])
        odom_trans.transform.rotation.x = quaternion[0]
        odom_trans.transform.rotation.y = quaternion[1]
        odom_trans.transform.rotation.z = quaternion[2]
        odom_trans.transform.rotation.w = quaternion[3]
        self.transform_broadcaster.sendTransform(odom_trans)
        # Set up the odometry
        odom = Odometry()
        odom.header.stamp = rospy.get_rostime()
        odom.header.frame_id = "odom"
        odom.pose.pose.position.x = x
        odom.pose.pose.position.y = y
        odom.pose.pose.position.z = 0.0
        odom.pose.pose.orientation = odom_trans.transform.rotation
        odom.child_frame_id = "base_link"
        [vx, vy, vz], [wx, wy, wz] = pybullet.getBaseVelocity(
            self.virtual_pepper.robot_model,
            self.virtual_pepper.getPhysicsClientId())
        odom.twist.twist.linear.x = vx
        odom.twist.twist.linear.y = vy
        odom.twist.twist.angular.z = wz
        self.odom_pub.publish(odom)

    def _broadcastCamera(self):
        """
        INTERNAL METHOD, updates and broadcasts the camera image data and the
        camera info data
        """
        try:
            camera = self.virtual_pepper.getActiveCamera()
            assert camera is not None
            assert camera.getFrame() is not None

            camera_image_msg = self.image_bridge.cv2_to_imgmsg(
                camera.getFrame())
            camera_image_msg.header.frame_id = camera.getCameraLink().getName()

            camera_info_msg = CameraInfo()
            camera_info_msg.distortion_model = "plumb_bob"
            camera_info_msg.header.frame_id = camera.getCameraLink().getName()
            camera_info_msg.width = camera.getResolution().width
            camera_info_msg.height = camera.getResolution().height
            camera_info_msg.D = [0.0, 0.0, 0.0, 0.0, 0.0]
            camera_info_msg.K = camera._getCameraIntrinsics()
            camera_info_msg.R = [1, 0, 0, 0, 1, 0, 0, 0, 1]
            camera_info_msg.P = list(camera_info_msg.K)
            camera_info_msg.P.insert(3, 0.0)
            camera_info_msg.P.insert(7, 0.0)
            camera_info_msg.P.append(0.0)

            if camera.getCameraId() == PepperVirtual.ID_CAMERA_TOP:
                camera_image_msg.encoding = "bgr8"
                self.front_cam_pub.publish(camera_image_msg)
                self.front_info_pub.publish(camera_info_msg)
            elif camera.getCameraId() == PepperVirtual.ID_CAMERA_BOTTOM:
                camera_image_msg.encoding = "bgr8"
                self.bottom_cam_pub.publish(camera_image_msg)
                self.bottom_info_pub.publish(camera_info_msg)
            elif camera.getCameraId() == PepperVirtual.ID_CAMERA_DEPTH:
                camera_image_msg.encoding = "16UC1"
                self.depth_cam_pub.publish(camera_image_msg)
                self.depth_info_pub.publish(camera_info_msg)

        except AssertionError:
            pass

    def _getJointStateMsg(self):
        """
        INTERNAL METHOD, returns the JointState of each robot joint
        """
        msg_joint_state = JointState()
        msg_joint_state.header = Header()
        msg_joint_state.header.stamp = rospy.get_rostime()
        msg_joint_state.name = list(self.virtual_pepper.joint_dict)
        msg_joint_state.position = self.virtual_pepper.getAnglesPosition(
            msg_joint_state.name)
        msg_joint_state.name += ["WheelFL", "WheelFR", "WheelB"]
        msg_joint_state.position += [0, 0, 0]
        return msg_joint_state

    def _jointAnglesCallback(self, msg):
        """
        INTERNAL METHOD, callback triggered when a message is received on the
        /joint_angles topic

        Parameters:
            msg - a ROS message containing a pose stamped with a speed
            associated to it. The type of the message is the following:
            naoqi_bridge_msgs::PoseStampedWithSpeed. That type can be found in
            the ros naoqi software stack
        """
        joint_list = msg.joint_names
        position_list = list(msg.joint_angles)

        if len(msg.speeds) != 0:
            velocity = list(msg.speeds)
        else:
            velocity = msg.speed

        self.virtual_pepper.setAngles(joint_list, position_list, velocity)

    def _velocityCallback(self, msg):
        """
        INTERNAL METHOD, callback triggered when a message is received on the
        /cmd_vel topic

        Parameters:
            msg - a ROS message containing a Twist command
        """
        self.virtual_pepper.move(msg.linear.x, msg.linear.y, msg.angular.z)

    def _moveToCallback(self, msg):
        """
        INTERNAL METHOD, callback triggered when a message is received on the
        '/move_base_simple/goal' topic. It allows to move the robot's base

        Parameters:
            msg - a ROS message containing a pose stamped with a speed
            associated to it. The type of the message is the following:
            naoqi_bridge_msgs::PoseStampedWithSpeed. That type can be found in
            the ros naoqi software stack
        """
        x = msg.pose_stamped.pose.position.x
        y = msg.pose_stamped.pose.position.y
        theta = pybullet.getEulerFromQuaternion([
            msg.pose_stamped.pose.orientation.x,
            msg.pose_stamped.pose.orientation.y,
            msg.pose_stamped.pose.orientation.z,
            msg.pose_stamped.pose.orientation.w])[-1]

        speed = msg.speed_percentage *\
            PepperBaseController.MAX_LINEAR_VELOCITY +\
            PepperBaseController.MIN_LINEAR_VELOCITY

        frame = msg.referenceFrame
        self.virtual_pepper.moveTo(
            x,
            y,
            theta,
            frame=frame,
            speed=speed,
            _async=True)

    def _killMoveCallback(self, msg):
        """
        INTERNAL METHOD, callback triggered when a message is received on the
        '/move_base_simple/cancel' topic. This callback is used to stop the
        robot's base from moving

        Parameters:
            msg - an empty ROS message, with the Empty type
        """
        self.virtual_pepper.moveTo(0, 0, 0, _async=True)

    def _spin(self):
        """
        INTERNAL METHOD, designed to emulate a ROS spin method
        """
        rate = rospy.Rate(self.frequency)

        try:
            while not self._wrapper_termination:
                rate.sleep()
                self.joint_states_pub.publish(self._getJointStateMsg())
                self._updateLasers()
                self._broadcastOdom()
                self._broadcastCamera()

        except Exception as e:
            print("Stopping the ROS wrapper: " + str(e))
